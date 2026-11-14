"""First tool: facts about the current session. Reads no files, changes nothing.

``@function_tool`` turns a Python function into a tool the model can call:
* the function name becomes the tool name,
* the docstring becomes the description the model reads to decide when to call it,
* type hints become the JSON schema of the arguments (here there are none).
The first parameter ``ctx: RunContextWrapper[...]`` is filled in by the SDK and
is not part of the schema, so the model cannot set it.
"""

from __future__ import annotations

import json
from datetime import datetime

from agents import RunContextWrapper, function_tool

from champion import __version__
from champion.core.context import ChampionContext


def describe_session(context: ChampionContext) -> dict:
    """Plain function (easy to test); the tool below only wraps it."""
    agents = context.config.get("agents") or {}
    return {
        "champion_version": __version__,
        "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "session_started": context.started_at.isoformat(timespec="seconds"),
        "working_directory": str(context.working_directory),
        "access_level": context.access_level,
        "access_level_enforced": False,  # enforced from step 2
        "enabled_specialists": sorted(name for name, a in agents.items() if a.get("enabled", True)),
        "specialists_available_now": [],  # added in steps 3 and 4
        "model": context.config.get("llm.model_id"),
    }


@function_tool
def session_info(ctx: RunContextWrapper[ChampionContext]) -> str:
    """Get facts about the current Champion session: version, local date and time,
    working directory, selected access level, configured specialists and model.
    Use it when the user asks about the session, the time or date, the folder or
    the access level. It reads no files."""
    return json.dumps(describe_session(ctx.context), indent=2)
