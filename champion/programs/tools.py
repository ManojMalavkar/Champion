"""Program tools for the agents (M2.5).

At chat start ``attach_program_tools`` reads every declared program, keeps the
trusted ones and adds them to the agent named in their entry (a group or one of its
sub-agents). Programs that are untrusted, changed, missing, invalid or for an agent
that is not offered are reported like specialist problems (shown at start), never
added.

One agent with up to ``programs.max_direct_tools`` programs gets one tool each,
``program_<name>``, whose arguments are the program's parameters. With more, it gets
``programs_list`` (names, classes, parameters) and ``program_run(name, arguments)``
instead, so the model's tool list stays short.

Policy: ``program_<name>`` has right edit only when the program takes paths it may
change (path parameters with ``access: edit``), otherwise read; approval ``always``
for delete, remote and ``approval: always`` (remote is its own permission, not local
edit access);
``program_run`` is registered with right read and checks the program's own class
before anything runs. Every call goes through ``run_guarded`` (audit row with the
program, hash, command and exit code; output cap).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Iterable

from agents import FunctionTool

from champion.policy.tool_policy import ToolRule, register, replace
from champion.programs.manifest import TRUSTED, program_states
from champion.programs.runner import run_program
from champion.programs.spec import ProgramSpec
from champion.specialists.base import SpecialistSpec, walk_specs
from champion.tools.changes import agent_name
from champion.tools.guard import ToolError, run_guarded

LIST_TOOL, RUN_TOOL = "programs_list", "program_run"
CLASS_TEXT = {"read": "reads only",
              "edit": "changes the files given as edit paths (listed in /changes; cannot be undone)",
              "delete": "deletes files; the user approves every run; cannot be undone",
              "remote": "acts on another system; the user approves every run; cannot be undone"}


def _arguments(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise ToolError("the arguments are not valid JSON") from None
    if not isinstance(value, dict):
        raise ToolError("the arguments must be an object")
    return value


def program_rule(spec: ProgramSpec) -> ToolRule:
    return ToolRule(spec.tool_name, spec.right, f"Trusted program {spec.name} ({spec.cls}, {spec.source})",
                    approval="always" if spec.asks else "never")


def describe(spec: ProgramSpec) -> str:
    return f"{spec.description} [program, class {spec.cls}: {CLASS_TEXT[spec.cls]}]"


def make_program_tool(spec: ProgramSpec) -> FunctionTool:
    replace(program_rule(spec))

    async def invoke(ctx, raw: str) -> str:
        try:
            args = _arguments(raw)
        except ToolError as exc:
            return f"ERROR: {exc}"
        audit = dict(args)  # run_program adds program, hash, command, exit code
        return await run_guarded(ctx, spec.tool_name, audit,
                                 lambda: run_program(ctx.context, spec, args, audit, agent_name(ctx)))

    return FunctionTool(name=spec.tool_name, description=describe(spec), params_json_schema=spec.schema(),
                        on_invoke_tool=invoke, strict_json_schema=True)


def _listing(specs: list[ProgramSpec]) -> str:
    lines = [f"{len(specs)} trusted program(s); run one with {RUN_TOOL}(name, arguments)."]
    for spec in specs:
        params = ", ".join(f"{p.name}: {p.type}{'' if p.required else '?'}"
                           + (f" ({p.access})" if p.type == "path" else "")
                           + (f" [{'|'.join(p.values)}]" if p.type == "enum" else "") for p in spec.params)
        lines.append(f"- {spec.name} [{spec.cls}]: {spec.description} ({params or 'no arguments'})")
    return "\n".join(lines)


def make_dispatch_tools(specs: list[ProgramSpec]) -> list[FunctionTool]:
    """``programs_list`` and ``program_run`` for an agent with many programs."""
    by_name = {spec.name: spec for spec in specs}
    register(ToolRule(LIST_TOOL, "none", "Lists the trusted programs of this agent (no file access)"))
    register(ToolRule(RUN_TOOL, "read", "Runs one trusted program; its class decides the right and approval"))

    async def invoke_list(ctx, raw: str) -> str:
        return await run_guarded(ctx, LIST_TOOL, {}, lambda: _listing(list(by_name.values())))

    async def invoke_run(ctx, raw: str) -> str:
        try:
            args = _arguments(raw)
            name = str(args.get("name") or "")
            values = json.loads(args.get("arguments") or "{}")
        except (ToolError, ValueError) as exc:
            return f"ERROR: {exc if isinstance(exc, ToolError) else 'arguments must be a JSON object text'}"
        audit: dict[str, Any] = {"name": name, "arguments": values}

        def work() -> str:
            spec = by_name.get(name)
            if spec is None:
                raise ToolError(f"there is no trusted program {name!r} here; see {LIST_TOOL}")
            return run_program(ctx.context, spec, values, audit, agent_name(ctx))

        return await run_guarded(ctx, RUN_TOOL, audit, work)

    run_schema = {"type": "object", "properties": {
        "name": {"type": "string", "description": "program name from programs_list"},
        "arguments": {"type": "string", "description": 'JSON object with the program\'s arguments, e.g. {"run_dir": "r1"}'}},
        "required": ["name", "arguments"], "additionalProperties": False}
    return [
        FunctionTool(name=LIST_TOOL, description="List the trusted programs you can run, with their arguments.",
                     params_json_schema={"type": "object", "properties": {}, "required": [],
                                         "additionalProperties": False},
                     on_invoke_tool=invoke_list, strict_json_schema=True),
        FunctionTool(name=RUN_TOOL, description="Run one trusted program by name (see programs_list).",
                     params_json_schema=run_schema, on_invoke_tool=invoke_run, strict_json_schema=True),
    ]


def tools_for(specs: list[ProgramSpec], max_direct: int) -> list[FunctionTool]:
    if len(specs) > max_direct:
        return make_dispatch_tools(specs)
    return [make_program_tool(spec) for spec in specs]


PROGRAM_RULES = """

Trusted programs (the program_* tools): the user declared and trusted these programs, so they
are the exception to "no scripts". When a task matches a program's description, call its tool
directly with the typed arguments. Do not ask the user for confirmation in your answer first:
Champion itself asks the user before every delete or remote program, and the tool answers
BLOCKED when the access level does not allow it (then report the BLOCKED reason). Report what
the program printed. What a program changes is listed in /changes but cannot be undone; say so."""


def programs_text(specs: list[ProgramSpec], max_direct: int) -> str:
    """Line added to an agent's description (seen by the orchestrator when it routes)."""
    shown = specs[:max_direct]
    items = "; ".join(f"{s.tool_name}: {s.description[:160]}" for s in shown)
    if len(specs) > len(shown):
        items += f"; and {len(specs) - len(shown)} more ({LIST_TOOL})"
    return f"It also runs the user's trusted programs (allowed, unlike other scripts) - send it these tasks: {items}"


def attach_program_tools(config: Any, specialists: Iterable[SpecialistSpec], working_directory: Path
                         ) -> tuple[list[SpecialistSpec], list[str]]:
    """Offered specialists with their trusted programs added, and the problems to show."""
    specialists = list(specialists)
    problems: list[str] = []
    agents = {item.name: item for spec in specialists for item in walk_specs(spec)}
    by_agent: dict[str, list[ProgramSpec]] = {}
    for state in program_states(config, working_directory):
        if state.status != TRUSTED:
            problems.append(f"program {state.name} not offered: {state.status}: {state.detail}")
            continue
        spec = state.spec
        agent = agents.get(spec.agent)
        if agent is None:
            problems.append(f"program {spec.name} not offered: agent {spec.agent} is not offered")
        elif spec.tool_name in agent.tool_names:
            problems.append(f"program {spec.name} not offered: {spec.agent} already has a tool {spec.tool_name}")
        else:
            by_agent.setdefault(spec.agent, []).append(spec)
    if not by_agent:
        return specialists, problems
    max_direct = int(config.get("programs.max_direct_tools", 15))
    extra = {name: tools_for(specs, max_direct) for name, specs in by_agent.items()}

    def add(spec: SpecialistSpec) -> SpecialistSpec:
        members = tuple(add(member) for member in spec.members)
        tools = extra.get(spec.name, [])
        if not tools and members == spec.members:
            return spec
        # the orchestrator routes by the description: name the programs there, or it never asks this agent
        description, instructions = spec.description, spec.instructions
        if tools:
            description = f"{spec.description.rstrip()}\n{programs_text(by_agent[spec.name], max_direct)}"
            instructions = spec.instructions.rstrip() + PROGRAM_RULES  # call the tool; Champion asks the user
        return dataclasses.replace(spec, tools=(*spec.tools, *tools), members=members, description=description,
                                   instructions=instructions)

    return [add(spec) for spec in specialists], problems
