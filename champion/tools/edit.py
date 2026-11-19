"""Structured change tools (step 4a, PLAN 4a.1b): ``file_edit`` and ``files_organize``.

Both need a write level (1.2, 2.2 or custom with write). They do not ask for
approval; every change goes into the session change list and can be undone
(``/undo``). They cover the common changes, so the shell is not needed for
edits or moves.

``file_edit`` changes text in a file, streaming (``text_patch``): files of any
size up to ``pydyna.max_model_bytes`` (8 GiB), memory about one chunk. Step
4d: a list of edits (``replace``, ``delete``, ``insert_before``,
``insert_after``, ``replace_lines``), each matched against the file as it is
before the call; all are checked first, overlapping edits are refused, and
all are written in one atomic replacement - one change, one diff, one
``/undo``. The old call ``file_edit(path, old_text, new_text)`` is one
``replace``. Undo is a reverse patch (``edit.full_backup: true`` also keeps a
full copy). A higher-risk edit (``edit.approval_*``) asks the user once.

``files_organize`` creates folders and moves or renames files as one plan.
The whole plan is checked first (scope, write right, secrets, links, source
exists, destination free, no two moves to one destination); if any entry
fails, nothing runs. Existing files are never overwritten; nothing is deleted.
"""

from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel, ValidationError

from champion.core.context import ChampionContext
from champion.execution.approval import ApprovalRequest, ask
from champion.policy.scope import PolicyDenied, check_path, open_no_symlinks
from champion.policy.tool_policy import ToolRule, register
from champion.tools.changes import (agent_name, backup_folder, load_reverse_patch, new_group, record,
                                    save_reverse_patch)
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError, requires, run_guarded
from champion.tools.text_patch import (MAX_DIFF_LINES, MAX_NEW, MAX_OLD, apply_hunks, encode, file_sha256,
                                       line_range, preview, scan, sniff)

MAX_MOVES = 200
MAX_FOLDERS = 50

register(ToolRule("file_edit", "edit", "Replace, delete or insert text in a text file; several edits as one change "
                                       "(streaming, undoable)"))
register(ToolRule("files_organize", "edit", "Create folders and move or rename files as one checked plan"))


class Move(BaseModel):
    source: str
    destination: str


def require_write(context: ChampionContext) -> None:
    if not context.level.allows("edit"):
        raise PolicyDenied(f"access level {context.level.name} ({context.level.label}) does not allow editing "
                           "files; the user can switch with /level 1.2 or give a folder edit access with /access")


def _open_target(path: Path) -> tuple[int, int]:
    try:
        dir_fd = open_no_symlinks(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        raise ToolError(f"folder {path.parent} does not exist") from None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
    except FileNotFoundError:
        os.close(dir_fd)
        raise ToolError(f"{path} does not exist") from None
    except OSError as exc:
        os.close(dir_fd)
        raise ToolError(f"cannot open {path} ({exc.strerror})") from None
    return dir_fd, fd


def _full_copy(context: ChampionContext, fd: int, deadline: float, name: str) -> str:
    folder = backup_folder(context)
    copy = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}-{name}"
    out = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        position = 0
        while True:
            data = os.pread(fd, 1024 * 1024, position)
            if not data:
                break
            os.write(out, data)
            position += len(data)
            if time.monotonic() > deadline:
                raise ToolError("full backup copy stopped: time limit reached; the file is unchanged")
        os.fsync(out)
    finally:
        os.close(out)
    return str(copy)


# ------------------------------------------------------------------ file_edit

OPERATIONS = ("replace", "insert_before", "insert_after", "delete", "replace_lines")
MAX_EDITS = 20
MAX_OCCURRENCE = 1000
FIELDS = {  # the fields each operation takes (besides ``operation``)
    "replace": {"old_text", "new_text", "occurrence"},
    "delete": {"old_text", "occurrence"},
    "insert_before": {"anchor", "text", "occurrence", "idempotent"},
    "insert_after": {"anchor", "text", "occurrence", "idempotent"},
    "replace_lines": {"start_line", "end_line", "expected_text", "new_text"},
}
SHA256 = re.compile(r"[0-9a-fA-F]{64}")


class EditOperation(BaseModel):
    """One edit. Every edit is matched against the file as it is before the call."""
    operation: Literal["replace", "insert_before", "insert_after", "delete", "replace_lines"]
    old_text: str | None = None       # replace, delete: exact text, must match once (or give occurrence)
    new_text: str | None = None       # replace, replace_lines: the new text ("" deletes)
    anchor: str | None = None         # insert_before, insert_after: exact text to insert next to
    text: str | None = None           # insert_before, insert_after: the text to insert
    occurrence: int | None = None     # which match when the text appears several times (1 = first)
    idempotent: bool | None = None    # insert only if the text is not in the file yet
    start_line: int | None = None     # replace_lines: first line (1-based)
    end_line: int | None = None       # replace_lines: last line (inclusive)
    expected_text: str | None = None  # replace_lines: the current text of those lines (mandatory)


@dataclass
class Hunk:
    offset: int    # in the file before the call
    old: bytes
    new: bytes
    line: int      # for messages and the diff
    summary: str
    number: int    # position in the request (1-based)


def _byte_lines(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _parse_edits(edits) -> list[EditOperation]:
    if not isinstance(edits, list) or not edits:
        raise ToolError("edits must be a non-empty list of edits, for example "
                        "[{\"operation\": \"replace\", \"old_text\": \"...\", \"new_text\": \"...\"}]")
    if len(edits) > MAX_EDITS:
        raise ToolError(f"at most {MAX_EDITS} edits in one call (got {len(edits)}); nothing changed")
    parsed = []
    for number, edit in enumerate(edits, 1):
        try:
            op = edit if isinstance(edit, EditOperation) else EditOperation.model_validate(edit)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ())) or "edit"
            raise ToolError(f"edit {number}: {where}: {first.get('msg')} (operations: {', '.join(OPERATIONS)}); "
                            "nothing changed") from None
        given = {k for k, v in op.model_dump().items() if v is not None and k != "operation"}
        extra = given - FIELDS[op.operation]
        if extra:
            raise ToolError(f"edit {number} ({op.operation}) does not take {', '.join(sorted(extra))}; it takes "
                            f"{', '.join(sorted(FIELDS[op.operation]))}; nothing changed")
        parsed.append(op)
    return parsed


def _find(fd: int, text: str | None, fmt, what: str, occurrence: int | None, target: Path, deadline: float,
          prefix: str) -> tuple[int, int, bytes]:
    """(offset, line, bytes) of the one match (or the chosen occurrence) of ``text``."""
    if not isinstance(text, str) or text == "":
        verb = "replace" if what == "old_text" else "insert next to"
        raise ToolError(f"{prefix}{what} must not be empty: give the exact lines to {verb}")
    needle = encode(text, fmt, what)
    if len(needle) > MAX_OLD:
        raise ToolError(f"{prefix}{what} is longer than {human_size(MAX_OLD)}; use a smaller block")
    if occurrence is not None and not 1 <= occurrence <= MAX_OCCURRENCE:
        raise ToolError(f"{prefix}occurrence must be between 1 and {MAX_OCCURRENCE} (got {occurrence})")
    found = scan(fd, needle, deadline, keep=max(5, occurrence or 0))
    if found.count == 0:
        raise ToolError(f"{prefix}{what} was not found in {target}; nothing changed. It must match exactly "
                        "(spaces, line breaks, upper/lower case); read the file first")
    if found.count > 1 and occurrence is None:
        where = ", ".join(str(n) for n in found.lines)
        raise ToolError(f"{prefix}{what} matches {found.count} times in {target} (first at lines {where}); it must "
                        "match exactly once. Add neighbouring lines to make it unique, or give occurrence "
                        "(1 = first); nothing changed")
    if occurrence is not None and occurrence > found.count:
        raise ToolError(f"{prefix}{what} matches {found.count} time(s) in {target}; occurrence {occurrence} "
                        "does not exist; nothing changed")
    index = (occurrence or 1) - 1
    return found.offsets[index], found.lines[index], needle


def _resolve(fd: int, fmt, op: EditOperation, number: int, target: Path, deadline: float,
             prefix: str) -> Hunk | None:
    """Turn one edit into a byte block of the current file; None = idempotent insert skipped."""
    kind = op.operation
    if kind == "replace":
        if op.new_text is None:
            raise ToolError(f"{prefix}replace needs new_text (empty text deletes the block)")
        if op.old_text == op.new_text:
            raise ToolError(f"{prefix}old_text and new_text are the same; nothing to change")
        offset, line, old = _find(fd, op.old_text, fmt, "old_text", op.occurrence, target, deadline, prefix)
        new = encode(op.new_text, fmt, "new_text")
        return Hunk(offset, old, new, line, f"{_byte_lines(old)} line(s) replaced by {_byte_lines(new)} line(s)",
                    number)
    if kind == "delete":
        offset, line, old = _find(fd, op.old_text, fmt, "old_text", op.occurrence, target, deadline, prefix)
        return Hunk(offset, old, b"", line, f"{_byte_lines(old)} line(s) deleted", number)
    if kind in ("insert_before", "insert_after"):
        if not isinstance(op.text, str) or op.text == "":
            raise ToolError(f"{prefix}text must not be empty: give the lines to insert")
        new = encode(op.text, fmt, "text")
        if op.idempotent and scan(fd, new, deadline, keep=1).count:
            return None
        offset, line, anchor = _find(fd, op.anchor, fmt, "anchor", op.occurrence, target, deadline, prefix)
        where = "before"
        if kind == "insert_after":
            offset, line, where = offset + len(anchor), line + anchor.count(b"\n"), "after"
        return Hunk(offset, b"", new, line, f"{_byte_lines(new)} line(s) inserted {where} the anchor", number)
    # replace_lines
    start, end = op.start_line, op.end_line
    if start is None or end is None or op.expected_text is None or op.new_text is None:
        raise ToolError(f"{prefix}replace_lines needs start_line, end_line, expected_text (the current text of those "
                        "lines; read them first) and new_text (empty deletes the lines)")
    if not 1 <= start <= end:
        raise ToolError(f"{prefix}start_line and end_line must be line numbers with 1 <= start_line <= end_line "
                        f"(got {start}-{end})")
    a, b, total = line_range(fd, start, end, deadline)
    if a < 0:
        raise ToolError(f"{prefix}lines {start}-{end} are not in {target} (it has {total} line(s)); nothing changed")
    if b - a > MAX_OLD:
        raise ToolError(f"{prefix}lines {start}-{end} are longer than {human_size(MAX_OLD)}; use a smaller range")
    region = os.pread(fd, b - a, a)
    expected = encode(op.expected_text, fmt, "expected_text")
    eol = b"\r\n" if region.endswith(b"\r\n") else (b"\n" if region.endswith(b"\n") else b"")
    if not (region == expected or (eol and region[:-len(eol)] == expected)):
        raise ToolError(f"{prefix}lines {start}-{end} of {target} are not expected_text (the file changed or the line "
                        "numbers are off); read those lines again; nothing changed")
    new = encode(op.new_text, fmt, "new_text")
    if new and eol and not new.endswith(b"\n"):
        new += eol  # keep the line break after the last replaced line
    if new == region:
        raise ToolError(f"{prefix}new_text is the same as lines {start}-{end}; nothing to change")
    count = end - start + 1
    summary = (f"lines {start}-{end}: {count} line(s) replaced by {_byte_lines(new)} line(s)" if new
               else f"lines {start}-{end}: {count} line(s) deleted")
    return Hunk(a, region, new, start, summary, number)


def _check_overlaps(hunks: list[Hunk]) -> list[Hunk]:
    ordered = sorted(hunks, key=lambda h: (h.offset, len(h.old)))
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.offset < prev.offset + len(prev.old) or (cur.offset == prev.offset and not prev.old and not cur.old):
            first, second = sorted((prev.number, cur.number))
            raise ToolError(f"edits {first} and {second} overlap or insert at the same place (line {cur.line}); "
                            "each edit must change a different part of the file; nothing changed")
    return ordered


def _combined_preview(fd: int, hunks: list[Hunk], fmt, name: str) -> str:
    parts = []
    for i, hunk in enumerate(hunks):
        text = preview(fd, hunk.offset, hunk.old, hunk.new, fmt, hunk.line, name)
        parts.append(text if i == 0 else text.split("\n", 2)[2])  # one ---/+++ header
    lines = "\n".join(parts).split("\n")
    limit = 2 * MAX_DIFF_LINES
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more diff lines not shown)"]
    return "\n".join(lines)


def _risk(context: ChampionContext, hunks: list[Hunk], size: int) -> str | None:
    """Why this edit needs the user's approval (PLAN 4d.0 decision 2), or None."""
    limit = context.config.get("edit.approval_max_deleted_lines")
    deleted = sum(max(0, _byte_lines(h.old) - _byte_lines(h.new)) for h in hunks)
    if deleted > limit:
        return f"deletes {deleted} lines (more than edit.approval_max_deleted_lines = {limit})"
    fraction = context.config.get("edit.approval_max_changed_fraction")
    changed = sum(max(len(h.old), len(h.new)) for h in hunks)
    if size >= context.config.get("edit.approval_min_bytes") and changed > fraction * size:
        return (f"changes about {round(100 * changed / size)}% of the file (more than "
                f"edit.approval_max_changed_fraction = {fraction:g})")
    return None


def _summary(target: Path, hunks: list[Hunk]) -> str:
    if len(hunks) == 1:
        return f"{target} at line {hunks[0].line}: {hunks[0].summary}"
    return f"{target}: {len(hunks)} edits in one change (" + "; ".join(f"line {h.line}: {h.summary}" for h in hunks) + ")"


def edit_file_ops(context: ChampionContext, path: str, edits: list, *, expected_sha256: str | None = None,
                  dry_run: bool = False, agent: str = "") -> str:
    """Check all edits, then write them in one atomic replacement (or show them with ``dry_run``)."""
    require_write(context)
    target = check_path(context.level, context.working_directory, path, "edit")
    ops = _parse_edits(edits)
    if expected_sha256 is not None and not (isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256)):
        raise ToolError("expected_sha256 must be the 64-character SHA-256 of the file (or left out)")
    cap = context.config.get("pydyna.max_model_bytes")
    seconds = context.config.get("pydyna.model_timeout_seconds")
    full = context.config.get("edit.full_backup")
    dir_fd, fd = _open_target(target)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ToolError(f"{target} is not a regular file")
        if before.st_size > cap:
            raise ToolError(f"{target} is {human_size(before.st_size)}, above the safety cap of {human_size(cap)} "
                            "(pydyna.max_model_bytes); nothing changed")
        fmt = sniff(fd)
        if expected_sha256 is not None:
            current = file_sha256(fd, time.monotonic() + seconds)
            if current != expected_sha256.lower():
                raise ToolError(f"{target} changed since it was read (SHA-256 is {current[:12]}..., expected "
                                f"{expected_sha256[:12].lower()}...); read it again; nothing changed")
        deadline = time.monotonic() + seconds
        hunks, skipped = [], []
        for number, op in enumerate(ops, 1):
            prefix = f"edit {number} ({op.operation}): " if len(ops) > 1 else ""
            hunk = _resolve(fd, fmt, op, number, target, deadline, prefix)
            (skipped.append(number) if hunk is None else hunks.append(hunk))
        if sum(len(h.new) for h in hunks) > MAX_NEW:
            raise ToolError(f"the new text is longer than {human_size(MAX_NEW)} in total; split the change")
        skipped_note = (f" Skipped edit(s) {', '.join(map(str, skipped))}: the text is already in the file."
                        if skipped else "")
        if not hunks:
            return f"Nothing changed in {target}:{skipped_note}"
        hunks = _check_overlaps(hunks)
        diff = _combined_preview(fd, hunks, fmt, str(target))
        if dry_run:
            return f"Dry run, nothing changed: {_summary(target, hunks)}.{skipped_note}\n{diff}"
        growth = sum(len(h.new) - len(h.old) for h in hunks)
        space = os.fstatvfs(dir_fd)
        available = space.f_bavail * space.f_frsize
        need = int(before.st_size * 1.05) + max(growth, 0) + (before.st_size if full else 0)
        if available < need:
            raise ToolError(f"not enough free disk space next to {target}: the edit needs about {human_size(need)}, "
                            f"{human_size(available)} is free; nothing changed")
        risk = _risk(context, hunks, before.st_size)
        if risk:
            ask(context, ApprovalRequest("file_edit", "Champion file_edit (no program is run)",
                                         ("file_edit", str(target)), "edit", str(target.parent),
                                         context.level.name, (str(target),),
                                         note=f"{risk}; {_summary(target, hunks)}"))
        full_copy = _full_copy(context, fd, time.monotonic() + seconds, target.name) if full else None
        size_after, digest = apply_hunks(fd, dir_fd, target.name, [(h.offset, h.old, h.new) for h in hunks],
                                         before=before, deadline=time.monotonic() + seconds)
    finally:
        os.close(fd)
        os.close(dir_fd)
    undo, note = None, "/undo restores it"
    try:
        record_file = save_reverse_patch(context, {
            "path": str(target), "hunks": [{"offset": h.offset, "old": h.old, "new": h.new} for h in hunks],
            "sha256_after": digest, "size_after": size_after, "full_copy": full_copy})
        undo = {"kind": "patch", "record": record_file}
    except (OSError, ToolError, PolicyDenied) as exc:
        note = f"undo is not available ({exc})"
    detail = (f"line {hunks[0].line}: {hunks[0].summary}" if len(hunks) == 1
              else f"{len(hunks)} edits: " + "; ".join(f"line {h.line}: {h.summary}" for h in hunks))
    number = record(context, tool="file_edit", action="modified", path=target, agent=agent,
                    size_before=before.st_size, size_after=size_after, detail=detail[:300], undo=undo,
                    group_id=new_group())
    return (f"Edited {_summary(target, hunks)}; size {human_size(before.st_size)} -> {human_size(size_after)}. "
            f"Change #{number} in /changes; {note}.{skipped_note}\nFile SHA-256 now {digest}.\n{diff}")


def edit_file(context: ChampionContext, path: str, old_text: str, new_text: str, agent: str = "") -> str:
    """The step 4a call: one exact block replaced (empty ``new_text`` deletes it)."""
    return edit_file_ops(context, path, [{"operation": "replace", "old_text": old_text, "new_text": new_text}],
                         agent=agent)


def undo_patch(context: ChampionContext, record_file: str) -> int:
    """Apply a saved reverse patch; refuses if the file changed since the edit. Returns the new size."""
    data = load_reverse_patch(record_file)
    target = check_path(context.level, context.working_directory, data["path"], "edit")
    seconds = context.config.get("pydyna.model_timeout_seconds")
    dir_fd, fd = _open_target(target)
    try:
        before = os.fstat(fd)
        if before.st_size != data["size_after"] or \
                file_sha256(fd, time.monotonic() + seconds) != data["sha256_after"]:
            raise ToolError(f"{target} changed since the edit; undo refused, nothing written")
        reverse, shift = [], 0  # forward blocks are sorted; their offsets move by the earlier growth
        for hunk in data["hunks"]:
            reverse.append((hunk["offset"] + shift, hunk["new"], hunk["old"]))
            shift += len(hunk["new"]) - len(hunk["old"])
        size, _ = apply_hunks(fd, dir_fd, target.name, reverse, before=before, deadline=time.monotonic() + seconds)
    finally:
        os.close(fd)
        os.close(dir_fd)
    return size


# ------------------------------------------------------------------ files_organize

def _is_folder(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def organize_files(context: ChampionContext, moves: list, create_folders: list[str] | None = None,
                   agent: str = "") -> str:
    require_write(context)
    create_folders = create_folders or []
    moves = [m if isinstance(m, Move) else Move(**m) for m in (moves or [])]
    if not moves and not create_folders:
        raise ToolError("nothing to do: give moves and/or create_folders")
    if len(moves) > MAX_MOVES or len(create_folders) > MAX_FOLDERS:
        raise ToolError(f"at most {MAX_MOVES} moves and {MAX_FOLDERS} folders per call")
    level, work = context.level, context.working_directory
    problems: list[str] = []
    folders: list[Path] = []
    existing_folders: list[Path] = []

    def checked(text: str, what: str) -> Path | None:
        try:
            return check_path(level, work, text, "edit")
        except PolicyDenied as exc:
            problems.append(f"{what} {text!r}: BLOCKED - {exc}")
            return None

    for text in create_folders:
        folder = checked(text, "folder")
        if folder is None:
            continue
        if os.path.lexists(folder):
            if _is_folder(folder):
                existing_folders.append(folder)
            else:
                problems.append(f"folder {text!r}: {folder} exists and is not a folder")
        elif folder not in folders:
            folders.append(folder)
    folders.sort(key=lambda p: len(p.parts))
    planned = set(folders)
    for folder in folders:
        if not (_is_folder(folder.parent) or folder.parent in planned):
            problems.append(f"folder {folder}: parent folder {folder.parent} does not exist (add it to create_folders)")

    plan: list[tuple[Path, Path]] = []
    sources, destinations = set(), set()
    for move in moves:
        source = checked(move.source, "source")
        destination = checked(move.destination, "destination")
        if source is None or destination is None:
            continue
        if not os.path.lexists(source):
            problems.append(f"source {move.source!r}: {source} does not exist")
            continue
        if source == work or source in work.parents:
            problems.append(f"source {move.source!r}: the working folder itself (or a parent) is never moved")
            continue
        if move.destination.endswith("/") or _is_folder(destination) or destination in planned:
            destination = destination / source.name
            try:
                destination = check_path(level, work, str(destination), "edit")
            except PolicyDenied as exc:
                problems.append(f"destination {destination}: BLOCKED - {exc}")
                continue
        if os.path.lexists(destination):
            problems.append(f"destination {destination} already exists; files are never overwritten")
        if destination == source or source in destination.parents:
            problems.append(f"{source} cannot be moved into itself")
        if not (_is_folder(destination.parent) or destination.parent in planned):
            problems.append(f"destination folder {destination.parent} does not exist (add it to create_folders)")
        if destination in destinations:
            problems.append(f"two moves go to {destination}")
        if source in sources:
            problems.append(f"{source} is moved twice")
        sources.add(source)
        destinations.add(destination)
        plan.append((source, destination))
    if problems:
        shown = problems[:20] + ([f"... and {len(problems) - 20} more"] if len(problems) > 20 else [])
        raise ToolError("the plan was checked and nothing was changed:\n- " + "\n- ".join(shown))

    group = new_group()
    deadline = time.monotonic() + context.config.get("execution.timeout_seconds")
    done: list[str] = []
    numbers: list[int] = []
    failure = ""
    steps = [("mkdir", folder, None) for folder in folders] + [("move", s, d) for s, d in plan]
    for index, (kind, first, second) in enumerate(steps):
        if time.monotonic() > deadline:
            failure = "time limit reached"
            break
        try:
            if kind == "mkdir":
                os.mkdir(first)
                numbers.append(record(context, tool="files_organize", action="created", path=first, agent=agent,
                                      undo={"kind": "mkdir", "path": str(first)}, group_id=group))
                done.append(f"  created  {first}/")
            else:
                if os.path.lexists(second):
                    raise ToolError(f"{second} appeared meanwhile; not overwritten")
                os.rename(first, second)
                action = "renamed" if first.parent == second.parent else "moved"
                numbers.append(record(context, tool="files_organize", action=action, path=first, new_path=second,
                                      agent=agent, undo={"kind": "move", "source": str(first),
                                                         "destination": str(second)}, group_id=group))
                done.append(f"  {action:<8} {first} -> {second}")
        except (OSError, ToolError) as exc:
            reason = "different file system (use a copy instead)" if getattr(exc, "errno", None) == 18 else \
                (exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc))
            failure = f"step {index + 1} failed: {reason}"
            break
    header = f"Plan: {len(folders)} folder(s) to create, {len(plan)} move(s)"
    if existing_folders:
        header += f"; {len(existing_folders)} folder(s) already existed"
    lines = [header, *done]
    if failure:
        left = len(steps) - len(done)
        lines.append(f"STOPPED: {failure}; {len(done)} step(s) done, {left} not done. /undo takes back the done steps.")
    if numbers:
        span = f"#{numbers[0]}" if len(numbers) == 1 else f"#{numbers[0]}-#{numbers[-1]}"
        lines.append(f"Changes {span} in /changes; /undo takes this whole plan back.")
    return "\n".join(lines)


# ------------------------------------------------------------------ tools

def _edits_from(edits: list | None, old_text: str | None, new_text: str | None) -> list:
    if edits:
        if old_text is not None or new_text is not None:
            raise ToolError("give either edits, or old_text and new_text, not both; nothing changed")
        return list(edits)
    if old_text is None or new_text is None:
        raise ToolError("give edits (a list), or old_text and new_text for one replacement; nothing changed")
    return [{"operation": "replace", "old_text": old_text, "new_text": new_text}]


def _audit_arguments(path: str, edits: list | None, old_text: str | None, new_text: str | None,
                     expected_sha256: str | None, dry_run: bool | None) -> dict:
    """Sizes and operation names only, never the text itself."""
    items = [e.model_dump() if isinstance(e, BaseModel) else dict(e) for e in (edits or []) if e is not None]
    texts = ("old_text", "new_text", "anchor", "text", "expected_text")
    return {"path": path, "operations": [str(e.get("operation")) for e in items] or ["replace"],
            "chars": sum(len(e.get(k) or "") for e in items for k in texts if isinstance(e.get(k), str))
            + len(old_text or "") + len(new_text or ""),
            "expected_sha256": expected_sha256 is not None, "dry_run": bool(dry_run)}


@function_tool(is_enabled=requires("edit"))
async def file_edit(ctx: RunContextWrapper[ChampionContext], path: str, edits: list[EditOperation] | None = None,
                    old_text: str | None = None, new_text: str | None = None, expected_sha256: str | None = None,
                    dry_run: bool | None = None) -> str:
    """Change text in a text file (any size, LS-DYNA models included); read the file first.
    Operations in `edits` (each matched against the file as it is now; all or nothing, one
    change in /changes, one /undo):
    - replace: old_text -> new_text (new_text "" deletes); old_text must match exactly once,
      or give occurrence (1 = first match).
    - delete: old_text removed.
    - insert_before / insert_after: text inserted next to an exact anchor; idempotent=true
      skips it if the text is already in the file.
    - replace_lines: start_line..end_line (1-based, inclusive) replaced by new_text;
      expected_text (the current text of those lines) is mandatory. Prefer anchors.
    Edits must not overlap. For one replacement, old_text and new_text alone also work.
    A large change (many lines deleted, much of a big file) asks the user first.

    Args:
        path: File to edit; relative paths start at the working folder.
        edits: Up to 20 edits, for example [{"operation": "insert_after", "anchor": "import os\\n",
            "text": "import re\\n"}, {"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"}].
        old_text: One exact block to replace (up to 64 KiB), instead of edits.
        new_text: Its replacement (up to 1 MiB in total); empty deletes the block.
        expected_sha256: SHA-256 of the file as you read it; the edit is refused if it changed.
        dry_run: true shows the diff and changes nothing.
    """
    arguments = _audit_arguments(path, edits, old_text, new_text, expected_sha256, dry_run)
    return await run_guarded(ctx, "file_edit", arguments,
                             lambda: edit_file_ops(ctx.context, path, _edits_from(edits, old_text, new_text),
                                                   expected_sha256=expected_sha256, dry_run=bool(dry_run),
                                                   agent=agent_name(ctx)))


@function_tool(is_enabled=requires("edit"))
async def files_organize(ctx: RunContextWrapper[ChampionContext], moves: list[Move],
                         create_folders: list[str] | None = None) -> str:
    """Create folders and move or rename files as one checked plan. The whole plan is
    checked first; if any entry is not allowed, nothing is changed. Never overwrites a file
    and never deletes. A destination ending in '/' or an existing/new folder means "into
    that folder". Listed in /changes; /undo takes the whole plan back. Needs a write level.

    Args:
        moves: List of {source, destination} (at most 200), for example
            [{"source": "a.k", "destination": "models/"}, {"source": "run1", "destination": "run1_old"}].
        create_folders: Folders to create first, for example ["models", "runs"] (at most 50).
    """
    arguments = {"moves": [m.model_dump() for m in moves], "create_folders": create_folders}
    return await run_guarded(ctx, "files_organize", arguments,
                             lambda: organize_files(ctx.context, moves, create_folders, agent_name(ctx)))


EDIT_TOOLS = [file_edit, files_organize]
