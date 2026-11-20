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

Step M0 (parallel tracks) adds two things, so a track never edits shared files:

* **Groups:** a spec with ``members`` is a group (for example PyDyna with
  Keyword, Code and DPF-Post). The orchestrator sees one tool for the group;
  the group agent gets each member as its own tool ``<member>_agent``. Groups
  are at most two levels deep: a member cannot have members. Members are
  shown and audited as ``Group/Member`` and switched with
  ``agents.<member>.enabled``.
* **Own config section:** ``config_section`` with ``config_defaults``,
  ``config_validate`` and ``config_path_keys`` is registered with the config
  loader when the spec is registered, so the track's settings live in its own
  package (example values in ``config/examples/<section>.yaml``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping

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
    members: tuple["SpecialistSpec", ...] = ()  # M0: sub-agents of a group (one level only)
    config_section: str | None = None  # M0: own top-level config section, e.g. "hpc"
    config_defaults: Mapping[str, Any] = field(default_factory=dict, hash=False)  # every key of that section
    config_validate: Callable[[Mapping[str, Any]], list[str]] | None = field(default=None, compare=False)
    config_path_keys: tuple[str, ...] = ()  # keys of the section resolved against the project folder

    @property
    def tool_name(self) -> str:
        return f"{self.name}_agent"

    @property
    def tool_names(self) -> list[str]:
        return [getattr(tool, "name", repr(tool)) for tool in self.tools]

    @property
    def is_group(self) -> bool:
        return bool(self.members)


def walk_specs(spec: SpecialistSpec) -> Iterator[SpecialistSpec]:
    """The spec and all its members (a group and its sub-agents)."""
    yield spec
    for member in spec.members:
        yield from walk_specs(member)


def all_tools(spec: SpecialistSpec) -> list[Any]:
    """The tools of a spec and of all its members (for is_enabled and routing rules)."""
    return [tool for item in walk_specs(spec) for tool in item.tools]


def all_tool_names(spec: SpecialistSpec) -> list[str]:
    return [getattr(tool, "name", repr(tool)) for tool in all_tools(spec)]
