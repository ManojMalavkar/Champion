"""Audit log: one row per tool call, kept in a local SQLite file.

Ported from mChampion ``storage/audit.py``. Each row records when, which
agent and tool, the arguments the model sent, the access level, the decision
(``allow``, ``block`` or ``error``), the reason and the size of the result.
Tool results themselves are not stored (they can contain model data).

The file (``storage.audit_db``, default ``champion.db``) is created with mode
600; the file tools cannot read it (database files are always blocked).

Step 4a adds the session change list (table ``changes``): at a write level
writes run without an approval prompt, so every change is recorded here and
shown with ``/changes``. One list per session (the same ``session_id`` as the
audit rows). Only metadata is stored (paths, sizes, a short summary); the
reverse patches used by ``/undo`` are files under ``.champion/backups``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_ARGUMENT_CHARS = 2000

SCHEMA = """CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    agent TEXT,
    tool TEXT NOT NULL,
    arguments TEXT,
    access_level TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    result_chars INTEGER,
    seconds REAL
)"""

COLUMNS = ("session_id", "ts", "agent", "tool", "arguments", "access_level", "decision",
           "reason", "result_chars", "seconds")

CHANGES_SCHEMA = """CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    agent TEXT,
    tool TEXT NOT NULL,
    action TEXT NOT NULL,
    path TEXT NOT NULL,
    new_path TEXT,
    size_before INTEGER,
    size_after INTEGER,
    detail TEXT,
    undo TEXT,
    group_id TEXT,
    undone_by INTEGER
)"""

CHANGE_COLUMNS = ("id", "session_id", "ts", "agent", "tool", "action", "path", "new_path", "size_before",
                  "size_after", "detail", "undo", "group_id", "undone_by")
ACTIONS = ("created", "modified", "moved", "renamed", "copied", "permissions", "deleted", "compressed",
           "extracted", "undone")


class AuditLog:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            file = Path(self.path)
            file.parent.mkdir(parents=True, exist_ok=True)
            if not file.exists():
                os.close(os.open(file, os.O_CREAT | os.O_WRONLY, 0o600))
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self.session_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(SCHEMA)
            self._db.execute(CHANGES_SCHEMA)
            self._db.commit()

    def record(self, *, tool: str, arguments: dict, access_level: str, decision: str,
               reason: str = "", result_chars: int = 0, seconds: float = 0.0, agent: str = "") -> int | None:
        """Write one row; returns its row ID (M6: ``audit_id`` of the runtime state)."""
        args = json.dumps(arguments, default=str, ensure_ascii=False)[:MAX_ARGUMENT_CHARS]
        row = (self.session_id, datetime.now(timezone.utc).isoformat(timespec="milliseconds"), agent, tool,
               args, access_level, decision, reason[:MAX_ARGUMENT_CHARS], result_chars, round(seconds, 4))
        with self._lock:
            cursor = self._db.execute(
                f"INSERT INTO tool_events ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", row)
            self._db.commit()
            return cursor.lastrowid

    def recent(self, limit: int = 20, *, this_session: bool = True) -> list[dict]:
        query = f"SELECT {', '.join(COLUMNS)} FROM tool_events"
        params: tuple = ()
        if this_session:
            query += " WHERE session_id = ?"
            params = (self.session_id,)
        query += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(query, params + (limit,)).fetchall()
        return [dict(zip(COLUMNS, row)) for row in reversed(rows)]

    # ------------------------------------------------------------ change list

    def record_change(self, *, tool: str, action: str, path: str, new_path: str | None = None,
                      size_before: int | None = None, size_after: int | None = None, detail: str = "",
                      undo: dict | None = None, group_id: str = "", agent: str = "") -> int:
        """Add one change of this session; returns its number in the session list (1, 2, ...)."""
        if action not in ACTIONS:
            raise ValueError(f"unknown change action {action!r}")
        row = (self.session_id, datetime.now(timezone.utc).isoformat(timespec="milliseconds"), agent, tool,
               action, str(path), None if new_path is None else str(new_path), size_before, size_after,
               detail[:MAX_ARGUMENT_CHARS], None if undo is None else json.dumps(undo), group_id)
        with self._lock:
            self._db.execute("INSERT INTO changes (session_id, ts, agent, tool, action, path, new_path, "
                             "size_before, size_after, detail, undo, group_id) VALUES "
                             "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
            self._db.commit()
            (count,) = self._db.execute("SELECT COUNT(*) FROM changes WHERE session_id = ?",
                                        (self.session_id,)).fetchone()
        return count

    def session_changes(self) -> list[dict]:
        """This session's changes, oldest first, each with its ``number`` and decoded ``undo``."""
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(CHANGE_COLUMNS)} FROM changes WHERE session_id = ? "
                                    "ORDER BY id", (self.session_id,)).fetchall()
        result = []
        for number, row in enumerate(rows, start=1):
            item = dict(zip(CHANGE_COLUMNS, row))
            item["number"] = number
            item["undo"] = json.loads(item["undo"]) if item["undo"] else None
            result.append(item)
        return result

    def change_count(self) -> int:
        with self._lock:
            (count,) = self._db.execute("SELECT COUNT(*) FROM changes WHERE session_id = ?",
                                        (self.session_id,)).fetchone()
        return count

    def mark_undone(self, change_id: int, by_number: int) -> None:
        with self._lock:
            self._db.execute("UPDATE changes SET undone_by = ? WHERE id = ? AND session_id = ?",
                             (by_number, change_id, self.session_id))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
