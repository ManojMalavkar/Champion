"""Session change list and undo (step 4a, PLAN 4a.6b).

At a write level (1.2, 2.2, custom with write) writes run without an approval
prompt. Instead every change is recorded here (table ``changes`` of the audit
database) and shown to the user: one line after each turn with changes,
``/changes`` for the list, ``/undo`` and ``/undo N`` to take a change back.

Undo is offered for ``file_edit`` (reverse patch, checked with the file's
SHA-256), ``files_organize`` (moves back, created empty folders removed).
Writes through shell commands are listed but cannot be undone in step 4.
``/undo`` without a number undoes the last tool call's changes (its group);
``/undo N`` undoes entry N only. An undo needs a write level, checks the paths
again and is itself recorded.

Reverse patches are JSON files in ``<working folder>/.champion/backups/
<session>/`` (folder mode 700, files mode 600). ``.champion`` is blocked for
all agent tools, so no agent can change what ``/undo`` will write.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any

from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied, check_path, find_symlink
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError

INTERNAL = ".champion"


def agent_name(ctx: Any) -> str:
    return getattr(getattr(ctx, "agent", None), "name", "") or ""


def new_group() -> str:
    return uuid.uuid4().hex[:12]


def record(context: ChampionContext, *, tool: str, action: str, path: Path | str, agent: str = "",
           new_path: Path | str | None = None, size_before: int | None = None, size_after: int | None = None,
           detail: str = "", undo: dict | None = None, group_id: str = "") -> int:
    """Record one change; returns its number in this session's list (0 without an audit log)."""
    if context.audit is None:
        return 0
    return context.audit.record_change(tool=tool, action=action, path=str(path), agent=agent,
                                       new_path=None if new_path is None else str(new_path),
                                       size_before=size_before, size_after=size_after, detail=detail,
                                       undo=undo, group_id=group_id)


def backup_folder(context: ChampionContext) -> Path:
    """``<working folder>/.champion/backups/<session>``, created with mode 700."""
    session = context.audit.session_id if context.audit is not None else "no-session"
    folder = context.working_directory / INTERNAL / "backups" / session
    current = context.working_directory
    for part in (INTERNAL, "backups", session):
        current = current / part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ToolError(f"cannot create the undo folder {current} ({exc.strerror})") from None
    if find_symlink(folder) is not None:
        raise PolicyDenied(f"{folder} contains a symbolic link; undo data is not written there")
    return folder


def save_reverse_patch(context: ChampionContext, data: dict) -> str:
    folder = backup_folder(context)
    file = folder / f"{uuid.uuid4().hex}.json"
    payload = dict(data)
    for key in ("old", "new"):
        if key in payload:
            payload[key] = base64.b64encode(payload[key]).decode("ascii")
    if "hunks" in payload:  # step 4d: several blocks (forward offsets in the file before the edit)
        payload["hunks"] = [{"offset": h["offset"], "old": base64.b64encode(h["old"]).decode("ascii"),
                             "new": base64.b64encode(h["new"]).decode("ascii")} for h in payload["hunks"]]
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream)
    return str(file)


def load_reverse_patch(record_file: str) -> dict:
    with open(record_file, encoding="utf-8") as stream:
        data = json.load(stream)
    for key in ("old", "new"):
        if key in data:
            data[key] = base64.b64decode(data[key])
    if "hunks" in data:
        data["hunks"] = [{"offset": h["offset"], "old": base64.b64decode(h["old"]), "new": base64.b64decode(h["new"])}
                         for h in data["hunks"]]
    else:  # step 4a record: one block
        data["hunks"] = [{"offset": data["offset"], "old": data["old"], "new": data["new"]}]
    return data


# ------------------------------------------------------------------ display

def _size(value: int | None) -> str:
    return "-" if value is None else human_size(value)


def describe(change: dict) -> str:
    target = change["path"] + (f" -> {change['new_path']}" if change["new_path"] else "")
    sizes = ""
    if change["size_before"] is not None or change["size_after"] is not None:
        sizes = f" ({_size(change['size_before'])} -> {_size(change['size_after'])})"
    state = ""
    if change["undone_by"]:
        state = f" [undone by #{change['undone_by']}]"
    elif change["undo"] is None and change["action"] != "undone":
        state = " [no undo]"
    detail = f" - {change['detail']}" if change["detail"] else ""
    return (f"  #{change['number']:<3} {change['ts'][11:19]} {change['action']:<11} {target}{sizes}"
            f" [{change['agent'] or '-'}/{change['tool']}]{detail}{state}")


def changes_text(context: ChampionContext) -> str:
    changes = context.audit.session_changes() if context.audit is not None else []
    if not changes:
        return "No changes in this session yet."
    lines = [f"Changes in this session ({len(changes)}), newest first. /undo undoes the last tool call, "
             "/undo N one entry."]
    lines += [describe(c) for c in reversed(changes)]
    return "\n".join(lines)


def turn_summary(context: ChampionContext, count_before: int) -> str | None:
    if context.audit is None:
        return None
    new = context.audit.change_count() - count_before
    return f"  Changed files this turn: {new} (see /changes)" if new > 0 else None


# ------------------------------------------------------------------ undo

def _undo_one(context: ChampionContext, change: dict) -> str:
    undo = change["undo"]
    kind = undo.get("kind")
    if kind == "patch":
        from champion.tools.edit import undo_patch

        size = undo_patch(context, undo["record"])
        target = change["path"]
        record(context, tool="undo", action="undone", path=target, size_before=change["size_after"],
               size_after=size, detail=f"undo of #{change['number']} (file_edit)")
        return f"#{change['number']}: {target} restored ({human_size(size)})"
    if kind == "move":
        source = check_path(context.level, context.working_directory, undo["source"], "edit")
        destination = check_path(context.level, context.working_directory, undo["destination"], "edit")
        if not os.path.lexists(destination):
            raise ToolError(f"#{change['number']}: {destination} no longer exists; cannot move it back")
        if os.path.lexists(source):
            raise ToolError(f"#{change['number']}: {source} exists again; not overwritten")
        os.rename(destination, source)
        record(context, tool="undo", action="undone", path=destination, new_path=source,
               detail=f"undo of #{change['number']} ({change['action']})")
        return f"#{change['number']}: {destination} moved back to {source}"
    if kind == "mkdir":
        folder = check_path(context.level, context.working_directory, undo["path"], "edit")
        try:
            os.rmdir(folder)
        except FileNotFoundError:
            raise ToolError(f"#{change['number']}: {folder} no longer exists") from None
        except OSError:
            raise ToolError(f"#{change['number']}: {folder} is not empty; it was not removed") from None
        record(context, tool="undo", action="undone", path=folder,
               detail=f"undo of #{change['number']} (created folder removed)")
        return f"#{change['number']}: empty folder {folder} removed"
    raise ToolError(f"#{change['number']} cannot be undone")


def undo(context: ChampionContext, number: int | None = None) -> str:
    """Undo entry ``number``, or the last tool call's changes. Returns a report; raises on refusal."""
    if context.audit is None:
        raise ToolError("no change list in this session")
    if not context.level.allows("edit"):
        raise PolicyDenied(f"access level {context.level.name} ({context.level.label}) does not allow "
                           "editing; switch with /level 1.2 (or give the folder edit access with /access) to undo")
    changes = context.audit.session_changes()
    if not changes:
        raise ToolError("no changes in this session to undo")
    if number is not None:
        chosen = [c for c in changes if c["number"] == number]
        if not chosen:
            raise ToolError(f"there is no change #{number} in this session")
        if chosen[0]["action"] == "undone":
            raise ToolError(f"#{number} is itself an undo")
    else:
        last = next((c for c in reversed(changes) if c["action"] != "undone" and not c["undone_by"]), None)
        if last is None:
            raise ToolError("nothing left to undo in this session")
        chosen = [c for c in changes if last["group_id"] and c["group_id"] == last["group_id"]] or [last]
        chosen = list(reversed(chosen))
    blocked = [c for c in chosen if c["undo"] is None]
    if blocked:
        raise ToolError(f"#{blocked[0]['number']} ({blocked[0]['action']} by {blocked[0]['tool']}) cannot be "
                        "undone; only file_edit and files_organize changes can")
    done = [c for c in chosen if c["undone_by"]]
    if done:
        raise ToolError(f"#{done[0]['number']} was already undone by #{done[0]['undone_by']}")
    reports = []
    for change in chosen:
        try:
            reports.append(_undo_one(context, change))
        except (ToolError, PolicyDenied, OSError) as exc:
            reports.append(f"#{change['number']}: NOT undone - {exc}")
            break
        context.audit.mark_undone(change["id"], context.audit.change_count())
    return "Undo:\n" + "\n".join(f"  {r}" for r in reports)
