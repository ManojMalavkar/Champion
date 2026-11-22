"""L0 - runtime execution state of one request (track T8.1).

This is not engineering knowledge: it controls the request that is running
now. One JSON file per request in ``<working folder>/.champion/runtime/``
(``.champion`` is blocked for every agent tool, so only Champion itself reads
or changes it). It holds the request ID, the active agent and sub-agent, the
workflow and its current step, the tool calls (arguments plus a short result
excerpt), pending approvals and the pending action, the access scope, the
sandbox folder and its staged files, errors and retries, and cancellation.

When the request ends (``done``, ``failed``, ``cancelled``) the file is
moved to ``archive/`` without the result excerpts, or deleted. A file that is
still in the folder at the next start belongs to a request that never ended
(crash, killed terminal): ``unfinished()`` lists them.

Rules kept here, so every caller gets them:
* no step after the end; ``applying`` only when no approval is open;
* a declined approval clears the pending action (the change is not made);
* a staged file must be inside the sandbox folder;
* secrets in tool arguments, results and errors are redacted, never stored;
* the request ID is checked before it is used as a file name.

Later (see ``docs/tracks/memory.md``): in memory only, Redis for several
sessions or users, SQLite for workflows that must be resumed.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from champion.memory.storage import (MemoryRefused, claim_id, cut, ensure_dir, now_iso, read_json, redact,
                                     write_json)

STEPS = ("received", "planning", "running_tool", "awaiting_approval", "applying", "done", "failed", "cancelled")
FINAL_STEPS = ("done", "failed", "cancelled")
REQUEST_ID = re.compile(r"^REQ-\d{8}-\d{4,}$")
MAX_TOOL_CALLS = 200  # older entries are dropped from the file (the audit log keeps them all)
MAX_ERRORS = 50
CLAIM_GRACE_SECONDS = 30  # an empty request file younger than this is a start that is still writing
STALE_TEMP_SECONDS = 300  # left-over ".tmp-*.json" (a write stopped before its rename; a live write needs < 1 s)


def runtime_folder(working_directory: str | os.PathLike) -> Path:
    return Path(working_directory) / ".champion" / "runtime"


@dataclass
class RuntimeState:
    request_id: str
    agent: str
    sub_agent: str = ""
    workflow: str = ""
    current_step: str = "received"
    session_id: str = ""
    project_access: str = ""
    sandbox_path: str = ""
    staged_files: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)
    pending_action: dict | None = None
    errors: list[dict] = field(default_factory=list)
    retries: int = 0
    cancelled: bool = False
    started_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    _store: Any = field(default=None, repr=False, compare=False)

    # ---------------------------------------------------------------- state
    @property
    def finished(self) -> bool:
        return self.current_step in FINAL_STEPS

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("_store", None)
        return data

    @classmethod
    def from_dict(cls, data: dict, store: Any = None) -> "RuntimeState":
        known = {name for name in cls.__dataclass_fields__ if name != "_store"}
        missing = {"request_id", "agent"} - set(data)
        if missing:
            raise MemoryRefused(f"runtime state without {', '.join(sorted(missing))}")
        state = cls(**{key: value for key, value in data.items() if key in known})
        state._store = store
        return state

    def _save(self) -> None:
        self.updated_at = now_iso()
        if self._store is not None:
            self._store.save(self)

    def _check_open(self) -> None:
        if self.finished:
            raise MemoryRefused(f"request {self.request_id} has ended ({self.current_step}); start a new request")

    # ----------------------------------------------------------------- steps
    def set_step(self, step: str, *, agent: str | None = None, sub_agent: str | None = None) -> None:
        """Move to ``step`` (not a final one: use ``finish``/``cancel`` of the store)."""
        self._check_open()
        if step not in STEPS or step in FINAL_STEPS:
            raise MemoryRefused(f"unknown step {step!r}; steps: {', '.join(STEPS[:5])}")
        if step == "applying" and self.open_approvals():
            raise MemoryRefused("an approval is still open; the change cannot be applied before the user answers")
        if step == "awaiting_approval" and not self.open_approvals():
            raise MemoryRefused("no approval is open; ask for one with request_approval")
        self.current_step = step
        if agent is not None:
            self.agent = agent
        if sub_agent is not None:
            self.sub_agent = sub_agent
        self._save()

    # ------------------------------------------------------------ tool calls
    def record_tool_call(self, tool: str, arguments: dict | None = None, *, decision: str = "allow",
                         result: str | None = None, error: str = "", audit_id: int | None = None) -> None:
        self._check_open()
        limit = self._store.result_chars if self._store is not None else 2000
        entry = {"ts": now_iso(), "agent": self.sub_agent or self.agent, "tool": str(tool),
                 "arguments": redact(arguments or {}), "decision": decision,
                 "result_chars": 0 if result is None else len(result),
                 "result_excerpt": "" if result is None else cut(redact(str(result)), limit),
                 "error": cut(redact(str(error)), 1000) if error else ""}
        if audit_id is not None:
            entry["audit_id"] = audit_id
        self.tool_calls.append(entry)
        del self.tool_calls[:-MAX_TOOL_CALLS]
        if self.current_step == "received":
            self.current_step = "running_tool"
        self._save()

    # -------------------------------------------------------------- approvals
    def request_approval(self, *, tool: str, summary: str, action: dict | None = None,
                         fingerprint: str = "") -> str:
        """Record an approval request and the action it would allow; step becomes ``awaiting_approval``."""
        self._check_open()
        approval_id = fingerprint[:16] or uuid.uuid4().hex[:16]
        self.pending_approvals.append({"id": approval_id, "tool": tool, "summary": cut(redact(summary), 2000),
                                       "asked_at": now_iso(), "answer": None})
        if action is not None:
            self.pending_action = redact(dict(action)) | {"approval_id": approval_id}
        self.current_step = "awaiting_approval"
        self._save()
        return approval_id

    def open_approvals(self) -> list[dict]:
        return [item for item in self.pending_approvals if item["answer"] is None]

    def resolve_approval(self, approval_id: str, approved: bool) -> None:
        """Store the user's answer. A "no" clears the pending action it belonged to."""
        self._check_open()
        for item in self.pending_approvals:
            if item["id"] == approval_id and item["answer"] is None:
                item["answer"] = "approved" if approved else "declined"
                item["answered_at"] = now_iso()
                break
        else:
            raise MemoryRefused(f"no open approval {approval_id!r}")
        if not approved and self.pending_action and self.pending_action.get("approval_id") == approval_id:
            self.pending_action = None
        if not self.open_approvals():
            self.current_step = "applying" if approved and self.pending_action else "planning"
        self._save()

    # ---------------------------------------------------------------- sandbox
    def add_staged_file(self, path: str | os.PathLike) -> str:
        """Remember a file written to the sandbox; refused outside ``sandbox_path``."""
        self._check_open()
        if not self.sandbox_path:
            raise MemoryRefused("this request has no sandbox folder")
        root = os.path.realpath(self.sandbox_path)
        real = os.path.realpath(path)
        if real != root and not real.startswith(root.rstrip("/") + "/"):
            raise MemoryRefused(f"{path} is outside the sandbox folder {self.sandbox_path}")
        if real not in self.staged_files:
            self.staged_files.append(real)
        self._save()
        return real

    # ------------------------------------------------------- errors, retries
    def record_error(self, message: str, *, retry: bool = False) -> bool:
        """Store an error; with ``retry`` count one retry. Returns whether another retry is allowed."""
        self._check_open()
        self.errors.append({"ts": now_iso(), "step": self.current_step, "message": cut(redact(str(message)), 2000),
                            "retry": bool(retry)})
        del self.errors[:-MAX_ERRORS]
        if retry:
            self.retries += 1
        self._save()
        return self.can_retry

    @property
    def can_retry(self) -> bool:
        limit = self._store.max_retries if self._store is not None else 3
        return self.retries < limit


class RuntimeStateStore:
    """The runtime files of one working folder."""

    def __init__(self, folder: str | os.PathLike, *, keep_archived: int = 50, result_chars: int = 2000,
                 max_retries: int = 3) -> None:
        self.folder = Path(folder)
        self.archive_folder = self.folder / "archive"
        self.keep_archived = keep_archived
        self.result_chars = result_chars
        self.max_retries = max_retries

    @classmethod
    def for_working_directory(cls, working_directory: str | os.PathLike, **settings: Any) -> "RuntimeStateStore":
        return cls(runtime_folder(working_directory), **settings)

    def _path(self, request_id: str, *, archived: bool = False) -> Path:
        if not REQUEST_ID.match(request_id or ""):
            raise MemoryRefused(f"{request_id!r} is not a request ID (REQ-YYYYMMDD-NNNN)")
        return (self.archive_folder if archived else self.folder) / f"{request_id}.json"

    def start(self, *, agent: str, workflow: str = "", session_id: str = "", sub_agent: str = "",
              project_access: str = "", sandbox_path: str | os.PathLike | None = None) -> RuntimeState:
        request_id, _path = claim_id(self.folder, "REQ", 4, others=(self.archive_folder,))
        sandbox = ""
        if sandbox_path is not None:
            sandbox = str(ensure_dir(Path(sandbox_path)).resolve())
        state = RuntimeState(request_id=request_id, agent=agent, sub_agent=sub_agent, workflow=workflow,
                             session_id=session_id, project_access=project_access, sandbox_path=sandbox)
        state._store = self
        self.save(state)
        return state

    def save(self, state: RuntimeState) -> None:
        write_json(self._path(state.request_id), state.to_dict())

    def load(self, request_id: str) -> RuntimeState:
        path = self._path(request_id)
        if not path.is_file():
            archived = self._path(request_id, archived=True)
            if archived.is_file():
                return RuntimeState.from_dict(read_json(archived))  # read only: no store
            raise MemoryRefused(f"no runtime state {request_id}")
        return RuntimeState.from_dict(read_json(path), store=self)

    def unfinished(self) -> list[RuntimeState]:
        """Requests whose file is still here (still running, or ended without ``finish``)."""
        states = []
        for file in sorted(self.folder.glob("REQ-*.json")) if self.folder.is_dir() else ():
            try:
                states.append(RuntimeState.from_dict(read_json(file), store=self))
            except (MemoryRefused, TypeError, ValueError, OSError):
                # Empty or unreadable: Champion stopped between claiming the ID and its first write
                # (live check 22 Nov 2026: killed chat, 0-byte REQ file). Listed so it can be closed;
                # a very young one is a start in another chat that is still writing.
                state = self._broken(file)
                if state is not None:
                    states.append(state)
        return states

    def _broken(self, file: Path) -> RuntimeState | None:
        if not REQUEST_ID.match(file.stem):
            return None
        try:
            mtime = file.stat().st_mtime
        except OSError:
            return None
        if time.time() - mtime < CLAIM_GRACE_SECONDS:
            return None
        started = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(mtime))
        state = RuntimeState(request_id=file.stem, agent="unknown", started_at=started, updated_at=started,
                             errors=[{"ts": now_iso(), "step": "received", "retry": False,
                                      "message": "the request file was empty or unreadable (Champion stopped "
                                                 "before its first write)"}])
        state._store = self
        return state

    def remove_stale_temp_files(self, older_than: float = STALE_TEMP_SECONDS) -> int:
        """Delete ``.tmp-*.json`` left by a write that stopped before its rename. Returns how many."""
        removed = 0
        for place in (self.folder, self.archive_folder):
            for file in place.glob(".tmp-*.json") if place.is_dir() else ():
                try:
                    if time.time() - file.stat().st_mtime >= older_than and not file.is_symlink():
                        file.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    def finish(self, state: RuntimeState, status: str = "done", *, archive: bool = True) -> Path | None:
        """End the request: archive it without result excerpts (default) or delete it."""
        if status not in FINAL_STEPS:
            raise MemoryRefused(f"a request ends as one of {', '.join(FINAL_STEPS)}")
        state._check_open()
        state.current_step = status
        state.cancelled = state.cancelled or status == "cancelled"
        state.finished_at = state.updated_at = now_iso()
        for item in state.open_approvals():
            item["answer"] = "not answered"
        state.pending_action = None if status != "done" else state.pending_action
        active = self._path(state.request_id)
        target = None
        if archive:
            data = state.to_dict()
            for call in data["tool_calls"]:
                call.pop("result_excerpt", None)
            target = write_json(self._path(state.request_id, archived=True), data)
            self._prune_archive()
        active.unlink(missing_ok=True)
        state._store = None
        return target

    def cancel(self, state: RuntimeState, reason: str = "cancelled by the user") -> Path | None:
        state.errors.append({"ts": now_iso(), "step": state.current_step, "message": cut(redact(reason), 500),
                             "retry": False})
        state.cancelled = True
        return self.finish(state, "cancelled")

    def archived(self) -> list[str]:
        if not self.archive_folder.is_dir():
            return []
        return sorted(file.stem for file in self.archive_folder.glob("REQ-*.json"))

    def _prune_archive(self) -> None:
        files = sorted(self.archive_folder.glob("REQ-*.json"), key=lambda f: (f.stat().st_mtime, f.name))
        for file in files[:max(0, len(files) - self.keep_archived)]:
            file.unlink(missing_ok=True)
