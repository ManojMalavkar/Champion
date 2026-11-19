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

Step 4e: ``level`` is the per-folder access list (``policy/access.py``);
the old levels are presets of it. ``access_file`` is where ``/access save``
writes (``None`` = saving is off, as in tests); ``access_warnings`` are
folders from the config or the saved file that were skipped at start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from champion.core.config import Config
from champion.policy.access import PRESETS, AccessLevel, build_access, preset_level

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
    access_file: Path | None = None  # where /access save writes; None = saving off
    access_warnings: list[str] = field(default_factory=list)

    @property
    def access_level(self) -> str:
        return self.level.name

    def set_level(self, name: str) -> AccessLevel:
        """Apply a preset (keeps the user's folders); ``custom`` is the converted ``policy.custom``."""
        key = str(name).strip().lower()
        level = self.level.with_preset(key) if key in PRESETS else preset_level(key, self.config)
        return self.set_access(level, f"level {key}", {"command": f"/level {key}"})

    def set_access(self, level: AccessLevel, change: str, arguments: dict | None = None) -> AccessLevel:
        """Replace the access list (user action only) and write one audit row."""
        old = self.level
        self.level = level
        if self.audit is not None:
            self.audit.record(tool="/access", arguments=arguments or {"change": change}, access_level=level.name,
                              decision="allow", reason=f"{change}: {old.describe()} -> {level.describe()}",
                              agent="user")
        return level

    @classmethod
    def from_config(cls, config: Config, *, level: str | None = None,
                    audit: "AuditLog | None" = None, access_file: Path | None = None,
                    read: tuple[str, ...] = (), edit: tuple[str, ...] = (),
                    outside: str | None = None) -> "ChampionContext":
        access, warnings = build_access(config, level=level, saved_file=access_file,
                                        read=read, edit=edit, outside=outside)
        return cls(
            config=config,
            working_directory=Path(os.path.realpath(config.get("execution.working_directory"))),
            level=access,
            audit=audit,
            access_file=access_file,
            access_warnings=warnings,
        )
