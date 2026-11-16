"""Run context: Champion's own data that tools can use but the model never sees.

The Agents SDK passes one context object through a whole run. Tools receive it
as ``RunContextWrapper[ChampionContext]`` (``ctx.context``). Nothing in it is
sent to the model unless a tool chooses to return it. The access level stored
here is what every tool checks before touching a file (step 2); only the user
can change it (``--level`` or ``/level``), never the model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
