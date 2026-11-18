"""Structured change tools (step 4a, PLAN 4a.1b): ``file_edit`` and ``files_organize``.

Both need a write level (1.2, 2.2 or custom with write). They do not ask for
approval; every change goes into the session change list and can be undone
(``/undo``). They cover the common changes, so the shell is not needed for
edits or moves.

``file_edit`` replaces one exact block of text, streaming (``text_patch``):
files of any size up to ``pydyna.max_model_bytes`` (8 GiB), memory about one
chunk. ``old_text`` must match exactly once; the result shows a small diff.
Undo is a reverse patch (``edit.full_backup: true`` also keeps a full copy).

``files_organize`` creates folders and moves or renames files as one plan.
The whole plan is checked first (scope, write right, secrets, links, source
exists, destination free, no two moves to one destination); if any entry
fails, nothing runs. Existing files are never overwritten; nothing is deleted.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied, check_path, open_no_symlinks
from champion.policy.tool_policy import ToolRule, register
from champion.tools.changes import (agent_name, backup_folder, load_reverse_patch, new_group, record,
                                    save_reverse_patch)
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError, requires, run_guarded
from champion.tools.text_patch import (MAX_NEW, MAX_OLD, apply_patch, encode, file_sha256, preview, scan,
                                       sniff)

MAX_MOVES = 200
MAX_FOLDERS = 50

register(ToolRule("file_edit", "write", "Replace one exact block of text in a text file (streaming, undoable)"))
register(ToolRule("files_organize", "write", "Create folders and move or rename files as one checked plan"))


class Move(BaseModel):
    source: str
    destination: str


def require_write(context: ChampionContext) -> None:
    if not context.level.allows("write"):
        raise PolicyDenied(f"access level {context.level.name} ({context.level.label}) does not allow writing "
                           "files; the user can switch to a write level with /level 1.2")


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


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


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

def edit_file(context: ChampionContext, path: str, old_text: str, new_text: str, agent: str = "") -> str:
    require_write(context)
    target = check_path(context.level, context.working_directory, path, "write")
    if not isinstance(old_text, str) or old_text == "":
        raise ToolError("old_text must not be empty: give the exact lines to replace")
    if old_text == new_text:
        raise ToolError("old_text and new_text are the same; nothing to change")
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
        old, new = encode(old_text, fmt, "old_text"), encode(new_text, fmt, "new_text")
        if len(old) > MAX_OLD:
            raise ToolError(f"old_text is longer than {human_size(MAX_OLD)}; edit a smaller block")
        if len(new) > MAX_NEW:
            raise ToolError(f"new_text is longer than {human_size(MAX_NEW)}; edit a smaller block")
        found = scan(fd, old, time.monotonic() + seconds)
        if found.count == 0:
            raise ToolError(f"old_text was not found in {target}; nothing changed. It must match exactly "
                            "(spaces, line breaks, upper/lower case); read the file first")
        if found.count > 1:
            where = ", ".join(str(n) for n in found.lines)
            raise ToolError(f"old_text matches {found.count} times in {target} (first at lines {where}); it must "
                            "match exactly once. Add neighbouring lines to make it unique; nothing changed")
        offset, line = found.offsets[0], found.lines[0]
        space = os.fstatvfs(dir_fd)
        available = space.f_bavail * space.f_frsize
        need = int(before.st_size * 1.05) + len(new) + (before.st_size if full else 0)
        if available < need:
            raise ToolError(f"not enough free disk space next to {target}: the edit needs about {human_size(need)}, "
                            f"{human_size(available)} is free; nothing changed")
        diff = preview(fd, offset, old, new, fmt, line, str(target))
        full_copy = _full_copy(context, fd, time.monotonic() + seconds, target.name) if full else None
        size_after, digest = apply_patch(fd, dir_fd, target.name, offset=offset, old=old, new=new,
                                         before=before, deadline=time.monotonic() + seconds)
    finally:
        os.close(fd)
        os.close(dir_fd)
    undo, note = None, "/undo restores it"
    try:
        record_file = save_reverse_patch(context, {"path": str(target), "offset": offset, "old": old, "new": new,
                                                   "sha256_after": digest, "size_after": size_after,
                                                   "full_copy": full_copy})
        undo = {"kind": "patch", "record": record_file}
    except (OSError, ToolError, PolicyDenied) as exc:
        note = f"undo is not available ({exc})"
    n_old, n_new = _line_count(old_text), _line_count(new_text)
    number = record(context, tool="file_edit", action="modified", path=target, agent=agent,
                    size_before=before.st_size, size_after=size_after,
                    detail=f"line {line}: {n_old} line(s) replaced by {n_new}", undo=undo, group_id=new_group())
    return (f"Edited {target} at line {line}: {n_old} line(s) replaced by {n_new} line(s); size "
            f"{human_size(before.st_size)} -> {human_size(size_after)}. Change #{number} in /changes; {note}.\n"
            f"{diff}")


def undo_patch(context: ChampionContext, record_file: str) -> int:
    """Apply a saved reverse patch; refuses if the file changed since the edit. Returns the new size."""
    data = load_reverse_patch(record_file)
    target = check_path(context.level, context.working_directory, data["path"], "write")
    seconds = context.config.get("pydyna.model_timeout_seconds")
    dir_fd, fd = _open_target(target)
    try:
        before = os.fstat(fd)
        if before.st_size != data["size_after"] or \
                file_sha256(fd, time.monotonic() + seconds) != data["sha256_after"]:
            raise ToolError(f"{target} changed since the edit; undo refused, nothing written")
        size, _ = apply_patch(fd, dir_fd, target.name, offset=data["offset"], old=data["new"], new=data["old"],
                              before=before, deadline=time.monotonic() + seconds)
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
            return check_path(level, work, text, "write")
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
                destination = check_path(level, work, str(destination), "write")
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

@function_tool(is_enabled=requires("write"))
async def file_edit(ctx: RunContextWrapper[ChampionContext], path: str, old_text: str, new_text: str) -> str:
    """Replace one exact block of text in a text file (any size, LS-DYNA models included).
    old_text must appear exactly once in the file, with the same spaces and line breaks;
    read the file first. The change is listed in /changes and can be undone with /undo.
    Needs a write level.

    Args:
        path: File to edit; relative paths start at the working folder.
        old_text: The exact lines to replace (up to 64 KiB); must match exactly once.
        new_text: The replacement lines (up to 1 MiB); empty deletes the block.
    """
    arguments = {"path": path, "old_chars": len(old_text or ""), "new_chars": len(new_text or "")}
    return await run_guarded(ctx, "file_edit", arguments,
                             lambda: edit_file(ctx.context, path, old_text, new_text, agent_name(ctx)))


@function_tool(is_enabled=requires("write"))
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
