"""Audit log: one row per tool call, kept in a local SQLite file.

Ported from mChampion ``storage/audit.py``. Each row records when, which
agent and tool, the arguments the model sent, the access level, the decision
(``allow``, ``block`` or ``error``), the reason and the size of the result.
Tool results themselves are not stored (they can contain model data).

The file (``storage.audit_db``, default ``champion.db``) is created with mode
600; the file tools cannot read it (database files are always blocked).
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
            self._db.commit()

    def record(self, *, tool: str, arguments: dict, access_level: str, decision: str,
               reason: str = "", result_chars: int = 0, seconds: float = 0.0, agent: str = "") -> None:
        args = json.dumps(arguments, default=str, ensure_ascii=False)[:MAX_ARGUMENT_CHARS]
        row = (self.session_id, datetime.now(timezone.utc).isoformat(timespec="milliseconds"), agent, tool,
               args, access_level, decision, reason[:MAX_ARGUMENT_CHARS], result_chars, round(seconds, 4))
        with self._lock:
            self._db.execute(f"INSERT INTO tool_events ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", row)
            self._db.commit()

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

    def close(self) -> None:
        with self._lock:
            self._db.close()
