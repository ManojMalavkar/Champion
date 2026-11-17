"""How a specialist is described (step 3).

A specialist is a small agent with its own instructions and its own tools
(for example the Linux agent with the shell tool). It is not called by the
user directly: the orchestrator offers each enabled specialist to the model
as one tool, ``<name>_agent``, built with ``Agent.as_tool`` (agents as tools,
no handoffs). The orchestrator keeps control: it sends a task, gets the
specialist's answer back as the tool result, and writes the final reply.

A ``SpecialistSpec`` holds everything needed to build that tool. It contains
no SDK run state, so adding a specialist means writing one spec and
registering it (``registry.register_specialist``); the orchestrator, the
runtime, the chat and later the TUI pick it up without changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


@dataclass(frozen=True)
class SpecialistSpec:
    name: str  # config key agents.<name>.enabled and tool name <name>_agent, e.g. "linux_coder"
    title: str  # agent name shown in tool lines and the audit log, e.g. "Linux"
    description: str  # tells the orchestrator when to call it (becomes the tool description)
    instructions: str  # the specialist's own system prompt
    tools: tuple[Any, ...] = ()  # FunctionTools; each needs a tool-policy entry
    routing_hints: tuple[str, ...] = ()  # example requests that belong to this specialist
    enabled_by_default: bool = True
    max_turns: int | None = None  # None = orchestration.specialist_max_turns
    extra: dict[str, Any] = field(default_factory=dict, compare=False)  # for later steps (skills, MCP)

    @property
    def tool_name(self) -> str:
        return f"{self.name}_agent"

    @property
    def tool_names(self) -> list[str]:
        return [getattr(tool, "name", repr(tool)) for tool in self.tools]
