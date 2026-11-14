"""Run context: Champion's own data that tools can use but the model never sees.

The Agents SDK passes one context object through a whole run. Tools receive it
as ``RunContextWrapper[ChampionContext]`` (``ctx.context``). Nothing in it is
sent to the model unless a tool chooses to return it. From step 2 the access
level stored here is what every tool checks before touching a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from champion.core.config import Config


@dataclass
class ChampionContext:
    config: Config
    working_directory: Path
    access_level: str
    started_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @classmethod
    def from_config(cls, config: Config) -> "ChampionContext":
        return cls(
            config=config,
            working_directory=Path(config.get("execution.working_directory")),
            access_level=config.get("policy.default_level"),
        )
