"""View model of the TUI (T9.1): what the widgets show, read from the core.

No rules live here. Access rights come from ``ChampionContext.level``
(``policy/access.py``), changes from the audit log, usage from
``ChampionRuntime.total_usage`` and agents from the specialist registry. This
module only counts and names them, so every widget shows the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RUNTIME_STATES = ("idle", "thinking", "working", "error")
AGENT_STATES = ("idle", "thinking", "running", "waiting", "completed", "error")
ORCHESTRATOR = "Champion"
RENAMES = ("moved", "renamed", "undone")  # actions whose new_path replaces path


@dataclass
class AgentRow:
    name: str  # name in tool lines: "Champion", "Linux", "PyDyna/Keyword"
    tool: str  # tool name the orchestrator calls ("" for the orchestrator)
    depth: int  # 0 orchestrator, 1 specialist or group, 2 group member
    state: str = "idle"
    activity: str = ""


@dataclass
class ChampionUIState:
    runtime_status: str = "idle"
    working_context: Any = None  # ModelContext after CR-T9-4
    base_context: Any = None
    agents: list[AgentRow] = field(default_factory=list)
    agent_processes: list[Any] = field(default_factory=list)  # process registry after CR-T9-7
    files_affected: int = 0
    read_path_count: int = 0
    edit_path_count: int = 0
    llm_model: str = ""
    total_tokens: int = 0


def agent_rows(runtime: Any) -> list[AgentRow]:
    """The orchestrator and every offered specialist and group member (from the registry)."""
    rows = [AgentRow(ORCHESTRATOR, "", 0)]
    for spec in getattr(runtime, "specialists", None) or []:
        rows.append(AgentRow(spec.title, spec.tool_name, 1))
        rows += [AgentRow(f"{spec.title}/{member.title}", member.tool_name, 2) for member in spec.members]
    return rows


def path_counts(level: Any) -> tuple[int, int]:
    """(read paths, edit paths): the working folder and each listed folder. Edit counts as
    read too. "Everything else" is not a path and is not counted."""
    rights = [level.working, *(folder.right for folder in level.folders)]
    return sum(r in ("read", "edit") for r in rights), sum(r == "edit" for r in rights)


def files_affected(audit: Any) -> int:
    """Unique files changed in this session. A rename or move follows the file to its new
    name (one file, not two); a copy adds the new file."""
    if audit is None:
        return 0
    paths: set[str] = set()
    for change in audit.session_changes():
        new_path = change.get("new_path")
        if new_path and change["action"] in RENAMES:
            paths.discard(change["path"])
            paths.add(new_path)
        elif new_path and change["action"] == "copied":
            paths.add(new_path)
        else:
            paths.add(change["path"])
    return len(paths)


def build_state(runtime: Any, status: str = "idle", previous: ChampionUIState | None = None) -> ChampionUIState:
    context = runtime.context
    read, edit = path_counts(context.level)
    state = ChampionUIState(
        runtime_status=status,
        agents=agent_rows(runtime),
        files_affected=files_affected(context.audit),
        read_path_count=read,
        edit_path_count=edit,
        llm_model=str(runtime.config.get("llm.model_id")),
        total_tokens=runtime.total_usage.total_tokens,
    )
    if previous is not None:
        state.working_context, state.base_context = previous.working_context, previous.base_context
        state.agent_processes = previous.agent_processes
        known = {row.name: row for row in previous.agents}
        for row in state.agents:
            if row.name in known:
                row.state, row.activity = known[row.name].state, known[row.name].activity
    return state
