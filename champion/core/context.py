"""Run context: Champion's own data that tools can use but the model never sees.

The Agents SDK passes one context object through a whole run. Tools receive it
as ``RunContextWrapper[ChampionContext]`` (``ctx.context``). Nothing in it is
sent to the model unless a tool chooses to return it. The access level stored
here is what every tool checks before touching a file (step 2); only the user
can change it (``--level`` or ``/level``), never the model.

Specialists (step 3) run as nested runs with this same context object, so a
level change applies to them at once and their tool calls go to the same
audit log. ``recorder`` is the current turn's tool-event recorder, set by the
runtime for the length of one turn, so nested tool calls can be shown.

``approver`` (step 4a) is how a tool asks the user to approve a delete: the
chat sets it to a y/N prompt; it stays ``None`` in ``champion chat -q`` and
in tests, so anything that needs approval is refused there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from champion.core.config import Config
from champion.policy.levels import AccessLevel, get_level

if TYPE_CHECKING:
    from champion.storage.audit import AuditLog


@dataclass
class ChampionContext:
    config: Config
    working_directory: Path  # real path (symbolic links resolved once, at start)
    level: AccessLevel
    audit: "AuditLog | None" = None
    started_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    specialists: list[str] = field(default_factory=list)  # tool names of the offered specialists
    recorder: Any = None  # ToolEventRecorder of the running turn (None between turns)
    approver: Any = None  # callable(ApprovalRequest) -> bool; None = nobody can approve (non-interactive)

    @property
    def access_level(self) -> str:
        return self.level.name

    def set_level(self, name: str) -> AccessLevel:
        self.level = get_level(name, self.config)
        return self.level

    @classmethod
    def from_config(cls, config: Config, *, level: str | None = None,
                    audit: "AuditLog | None" = None) -> "ChampionContext":
        return cls(
            config=config,
            working_directory=Path(os.path.realpath(config.get("execution.working_directory"))),
            level=get_level(level or config.get("policy.default_level"), config),
            audit=audit,
        )
