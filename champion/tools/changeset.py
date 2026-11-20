"""Multi-file change sets (step 4d.5): ``change_set_apply``.

A coding task usually changes several files. A change set does it in four
phases, so nothing is half done when something is wrong:

1. **Check** every file first (the same checks as ``file_edit``,
   ``file_create`` and ``file_write``: access, links, secrets, size, hash,
   anchors, overlaps). One problem: nothing is written.
2. **Ask** the user once if the set is higher-risk (more than
   ``edit.approval_max_files`` files, a whole-file replacement, many lines
   deleted, much of a file changed).
3. **Stage** every new version in a temporary file next to its target.
4. **Commit** in order (atomic replace per file). If a commit fails, the files
   already committed are rolled back (each only if it is still exactly as the
   set left it) and the failure is reported.

A normal file system cannot change several files atomically, so a crash in
phase 4 can still leave some files changed; those are in ``/changes``. All
entries of a set share one group: ``/undo`` takes the whole set back, and it
checks every file before it changes any.
"""

from __future__ import annotations

import time
from typing import Literal

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel, ValidationError

from champion.core.context import ChampionContext
from champion.execution.approval import ask
from champion.policy.scope import PolicyDenied, check_path
from champion.policy.tool_policy import ToolRule, register
from champion.tools import edit as _edit
from champion.tools.changes import agent_name, new_group
from champion.tools.edit import EditOperation, FilePlan, require_write
from champion.tools.guard import ToolError, requires, run_guarded

MAX_FILES = 20
MAX_TITLE = 120
MAX_SET_DIFF_LINES = 400
FIELDS = {"edit": {"edits", "expected_sha256"}, "create": {"content", "create_parents"},
          "write": {"content", "expected_sha256"}}

register(ToolRule("change_set_apply", "edit", "Change several files as one checked, staged and undoable set"))


class FileChange(BaseModel):
    """One file of a change set."""
    path: str
    action: Literal["edit", "create", "write"]
    edits: list[EditOperation] | None = None   # edit: as in file_edit
    content: str | None = None                 # create, write: the complete text
    expected_sha256: str | None = None         # edit, write: required
    create_parents: bool | None = None         # create: also create missing folders


def _parse(files) -> list[FileChange]:
    if not isinstance(files, list) or not files:
        raise ToolError("files must be a non-empty list of {path, action, ...}; nothing changed")
    if len(files) > MAX_FILES:
        raise ToolError(f"at most {MAX_FILES} files in one change set (got {len(files)}); nothing changed")
    parsed = []
    for number, item in enumerate(files, 1):
        try:
            change = item if isinstance(item, FileChange) else FileChange.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ())) or "file"
            raise ToolError(f"file {number}: {where}: {first.get('msg')} (actions: edit, create, write); "
                            "nothing changed") from None
        given = {k for k, v in change.model_dump().items() if v is not None and k not in ("path", "action")}
        extra = given - FIELDS[change.action]
        if extra:
            raise ToolError(f"file {number} ({change.action} {change.path}) does not take {', '.join(sorted(extra))}; "
                            f"it takes {', '.join(sorted(FIELDS[change.action]))}; nothing changed")
        if change.action in ("edit", "write") and change.expected_sha256 is None:
            raise ToolError(f"file {number} ({change.path}): expected_sha256 is required for {change.action} (read "
                            "the file first); nothing changed")
        if change.action == "edit" and not change.edits:
            raise ToolError(f"file {number} ({change.path}): edit needs edits; nothing changed")
        if change.action in ("create", "write") and change.content is None:
            raise ToolError(f"file {number} ({change.path}): {change.action} needs content; nothing changed")
        parsed.append(change)
    return parsed


def _prepare(context: ChampionContext, change: FileChange) -> FilePlan:
    if change.action == "edit":
        return _edit.prepare_edit(context, change.path, list(change.edits), change.expected_sha256)
    if change.action == "create":
        return _edit.prepare_create(context, change.path, change.content, bool(change.create_parents))
    return _edit.prepare_write(context, change.path, change.content, change.expected_sha256)


def _lines(plans: list[FilePlan]) -> str:
    return "\n".join(f"  {i}. {p.summary}" for i, p in enumerate(plans, 1))


def _diffs(plans: list[FilePlan]) -> str:
    lines = "\n".join(p.diff for p in plans).split("\n")
    if len(lines) > MAX_SET_DIFF_LINES:
        lines = lines[:MAX_SET_DIFF_LINES] + [f"... ({len(lines) - MAX_SET_DIFF_LINES} more diff lines not shown)"]
    return "\n".join(lines)


def apply_change_set(context: ChampionContext, title: str, files: list, *, dry_run: bool = False,
                     agent: str = "") -> str:
    require_write(context)
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
        raise ToolError(f"title must be a short description of the change (1-{MAX_TITLE} characters)")
    title = " ".join(title.split())
    changes = _parse(files)
    targets = [check_path(context.level, context.working_directory, c.path, "edit") for c in changes]
    if len(set(targets)) != len(targets):
        twice = next(t for t in targets if targets.count(t) > 1)
        raise ToolError(f"{twice} is listed twice; put all edits of one file in one entry; nothing changed")
    plans: list[FilePlan] = []
    committed: list[FilePlan] = []
    try:
        for number, change in enumerate(changes, 1):
            try:
                plans.append(_prepare(context, change))
            except (ToolError, PolicyDenied) as exc:
                raise type(exc)(f"file {number} ({change.path}): {exc}; nothing changed") from None
        active = [p for p in plans if p.kind != "edit" or p.hunks]
        if not active:
            return f"Nothing changed by change set '{title}': every edit was already in place."
        if dry_run:
            return (f"Dry run of change set '{title}', nothing changed: {len(active)} file(s)\n{_lines(active)}\n"
                    f"{_diffs(active)}")
        reasons = [f"{p.target.name} {p.risk}" for p in active if p.risk]
        limit = context.config.get("edit.approval_max_files")
        if len(active) > limit:
            reasons.insert(0, f"changes {len(active)} files (more than edit.approval_max_files = {limit})")
        if reasons:
            ask(context, _edit.approval_request(context, "change_set_apply", active, "; ".join(reasons), title))
        deadline = time.monotonic() + context.config.get("pydyna.model_timeout_seconds")
        for plan in active:
            _edit.stage_plan(context, plan, deadline)
        for plan in active:
            try:
                _edit.commit_plan(plan)
            except (ToolError, PolicyDenied, OSError) as exc:
                return _roll_back(context, title, plan, exc, committed, agent)
            committed.append(plan)
    finally:
        for plan in plans:
            plan.close()
    group = new_group()
    numbers = [_edit.record_plan(context, plan, agent=agent, tool="change_set_apply", group=group,
                                 prefix=f"{title}: ")[0] for plan in active]
    span = f"#{numbers[0]}" if len(numbers) == 1 else f"#{numbers[0]}-#{numbers[-1]}"
    hashes = "\n".join(f"  {p.target}: SHA-256 now {p.staged.sha256}" for p in active)
    return (f"Applied change set '{title}': {len(active)} file(s), {span} in /changes; /undo takes the whole set "
            f"back.\n{_lines(active)}\n{hashes}\n{_diffs(active)}")


def _roll_back(context: ChampionContext, title: str, failed: FilePlan, exc: Exception, committed: list[FilePlan],
               agent: str) -> str:
    """A commit failed: take back the files already committed (newest first)."""
    kept = []
    for plan in reversed(committed):
        problem = _edit.rollback_plan(context, plan)
        if problem:
            kept.append((plan, problem))
    message = (f"Change set '{title}' stopped at {failed.target}: {exc}. "
               f"Rolled back {len(committed) - len(kept)} of {len(committed)} file(s) already written; "
               "the other files were not changed.")
    if kept:
        group = new_group()
        for plan, _ in kept:  # still changed: listed so the user sees them and can /undo them
            _edit.record_plan(context, plan, agent=agent, tool="change_set_apply", group=group,
                              prefix=f"{title} (partial, rollback failed): ")
        message += " NOT rolled back (listed in /changes): " + "; ".join(f"{p.target}: {why}" for p, why in kept)
    raise ToolError(message)


@function_tool(is_enabled=requires("edit"))
async def change_set_apply(ctx: RunContextWrapper[ChampionContext], title: str, files: list[FileChange],
                           dry_run: bool | None = None) -> str:
    """Change several files as one set (for example a module, its test and a config).
    Every file is checked first; if one is wrong, nothing changes. The new versions are
    staged, then written in order; if writing fails, the files already written are rolled
    back. One /undo takes the whole set back. Higher-risk sets ask the user once.
    Each file: {"path", "action": "edit" (edits + expected_sha256, as in file_edit),
    "create" (content, optional create_parents) or "write" (content + expected_sha256)}.

    Args:
        title: Short description of the change, shown in /changes (for example "Add timeout option").
        files: Up to 20 files, each path at most once.
        dry_run: true shows all diffs and changes nothing.
    """
    items = [f.model_dump() if isinstance(f, BaseModel) else dict(f) for f in (files or []) if f is not None]
    arguments = {"title": title, "files": [{"path": f.get("path"), "action": f.get("action"),
                                            "edits": len(f.get("edits") or []),
                                            "chars": len(f.get("content") or "")} for f in items],
                 "dry_run": bool(dry_run)}
    return await run_guarded(ctx, "change_set_apply", arguments,
                             lambda: apply_change_set(ctx.context, title, list(files or []), dry_run=bool(dry_run),
                                                      agent=agent_name(ctx)))
