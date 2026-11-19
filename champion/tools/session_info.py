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
from champion.policy.tool_policy import ToolRule, register
from champion.tools.guard import run_guarded

register(ToolRule("session_info", "none", "Facts about the session; reads no files"))


def describe_session(context: ChampionContext) -> dict:
    """Plain function (easy to test); the tool below only wraps it."""
    agents = context.config.get("agents") or {}
    return {
        "champion_version": __version__,
        "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "session_started": context.started_at.isoformat(timespec="seconds"),
        "working_directory": str(context.working_directory),
        "access_level": context.access_level,
        "access": context.level.describe(),
        "access_level_enforced": True,
        "access_list": {  # step 4e: what the user allowed, per place
            "working_folder": context.level.working,
            "folders": [{"number": i, "path": str(f.path), "right": f.right}
                        for i, f in enumerate(context.level.folders, 1)],
            "everything_else": context.level.outside,
        },
        "how_to_change_access": "only the user, in the chat: /access (show), /access add PATH read|edit|none, "
                                "/access set N read|edit|none, /access remove N, /access outside none|read|edit, "
                                "/access preset 1.1|1.2|2.1|2.2, /level NAME, /access save",
        "enabled_specialists": sorted(name for name, a in agents.items() if a.get("enabled", True)),
        "specialists_available_now": list(context.specialists),  # tools offered to the orchestrator
        "model": context.config.get("llm.model_id"),
    }


@function_tool
async def session_info(ctx: RunContextWrapper[ChampionContext]) -> str:
    """Get facts about the current Champion session: version, local date and time,
    working directory, the user's access list (where reading and editing are allowed, and the
    exact /access commands the user can type to change it),
    configured specialists and model. Use it when the user asks about the session,
    the time or date, the folder or the access level. It reads no files."""
    return await run_guarded(ctx, "session_info", {},
                             lambda: json.dumps(describe_session(ctx.context), indent=2))
