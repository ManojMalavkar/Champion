"""Specialist registry: which specialists exist and which are offered (step 3).

Specialists register themselves (``register_specialist``) when their package
is imported. ``select_specialists`` decides, for one run of Champion, which
ones the orchestrator gets:

* switched on in the config (``agents.<name>.enabled``, default from the spec);
* the spec passes ``check_spec``. A spec with a problem, for example a tool
  without a tool-policy entry, is not offered at all (deny by default) and the
  problem is reported so the chat can show it.

Step M0: specialists are found, not listed. ``load_builtin_specialists``
imports every module and package in ``champion/specialists/`` (except
``base``, ``registry`` and names starting with ``_``), so a track adds an agent
by adding its own package and never edits this file. A package that fails to
import is reported and left out; Champion still starts. Members of a group
are switched with ``agents.<member>.enabled``.
"""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil

from champion.core.config import Config, register_config_section, unregister_config_section
from champion.policy.tool_policy import has_rule
from champion.specialists.base import NAME_PATTERN, SpecialistSpec, walk_specs

NOT_SPECIALISTS = frozenset({"base", "registry"})

# Tools of the orchestrator itself; a specialist tool must not reuse these names.
RESERVED_TOOL_NAMES = frozenset({"session_info", "filesystem_list", "filesystem_read", "filesystem_disk_usage"})

_SPECS: dict[str, SpecialistSpec] = {}
_LOAD_PROBLEMS: dict[str, str] = {}  # module name -> why it could not be loaded


def register_specialist(spec: SpecialistSpec) -> SpecialistSpec:
    """Add a specialist (and its config section). The same name with a different spec is an error."""
    existing = _SPECS.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(f"specialist {spec.name} is already registered with a different spec")
    if spec.config_section:
        register_config_section(spec.config_section, spec.config_defaults, validate=spec.config_validate,
                                path_keys=spec.config_path_keys, owner=spec.name)
    _SPECS[spec.name] = spec
    return spec


def unregister_specialist(name: str) -> None:
    spec = _SPECS.pop(name, None)
    if spec is not None and spec.config_section:
        unregister_config_section(spec.config_section)


def registered_specialists() -> list[SpecialistSpec]:
    return [_SPECS[name] for name in sorted(_SPECS)]


def specialist_modules() -> list[str]:
    """Names of the modules and packages in champion/specialists/ that hold specialists."""
    import champion.specialists as package

    return sorted(info.name for info in pkgutil.iter_modules(package.__path__)
                  if info.name not in NOT_SPECIALISTS and not info.name.startswith("_"))


def load_builtin_specialists() -> None:
    """Import every specialist module and package so they register themselves (step M0)."""
    for name in specialist_modules():
        try:
            importlib.import_module(f"champion.specialists.{name}")
        except Exception as exc:  # a broken track must not stop Champion
            _LOAD_PROBLEMS[name] = f"specialist module {name} could not be loaded ({type(exc).__name__}: {exc})"
        else:
            _LOAD_PROBLEMS.pop(name, None)


def load_problems() -> list[str]:
    return [_LOAD_PROBLEMS[name] for name in sorted(_LOAD_PROBLEMS)]


def check_spec(spec: SpecialistSpec, depth: int = 0) -> list[str]:
    """Return the problems that stop a spec from being offered (empty list = fine).

    ``depth`` is 1 for a member of a group: members cannot have members (at most 2 levels).
    """
    problems = []
    if not NAME_PATTERN.match(spec.name):
        problems.append("name must be 2-31 lowercase letters, digits or _ and start with a letter")
    for field_name in ("title", "description", "instructions"):
        if not str(getattr(spec, field_name) or "").strip():
            problems.append(f"{field_name} is empty")
    if spec.tool_name in RESERVED_TOOL_NAMES:
        problems.append(f"tool name {spec.tool_name} is reserved")
    if spec.max_turns is not None and (not isinstance(spec.max_turns, int) or spec.max_turns < 1):
        problems.append("max_turns must be a positive whole number")
    seen = set()
    for name in spec.tool_names:
        if name in seen:
            problems.append(f"tool {name} is listed twice")
        seen.add(name)
        if not has_rule(name):
            problems.append(f"tool {name} has no policy entry")
    if spec.members and depth >= 1:
        problems.append("groups are at most 2 levels deep: a sub-agent cannot have sub-agents")
    if depth >= 1 and spec.config_section:
        problems.append("a sub-agent cannot declare a config section (declare it on the group)")
    member_names = set()
    for member in spec.members if depth == 0 else ():
        if member.name in member_names or member.name == spec.name:
            problems.append(f"sub-agent name {member.name} is used twice")
        member_names.add(member.name)
        if member.tool_name in seen:
            problems.append(f"sub-agent tool {member.tool_name} has the name of a tool of the group")
        problems += [f"sub-agent {member.name}: {p}" for p in check_spec(member, depth + 1)]
    return problems


def _enabled(config: Config, spec: SpecialistSpec) -> bool:
    return bool(config.get(f"agents.{spec.name}.enabled", spec.enabled_by_default))


def select_specialists(config: Config, specs: list[SpecialistSpec] | None = None
                       ) -> tuple[list[SpecialistSpec], list[str]]:
    """Specialists to offer and the problems of those left out (for display)."""
    selected, problems = [], []
    if specs is None:
        load_builtin_specialists()
        specs = registered_specialists()
        problems += load_problems()
    used: set[str] = set()  # names of offered agents and sub-agents (tool names must be unique)
    for spec in specs:
        if not _enabled(config, spec):
            continue
        if spec.members:
            members = tuple(m for m in spec.members if _enabled(config, m))
            if not members and not spec.tools:
                continue  # every sub-agent switched off: nothing the group could do
            spec = dataclasses.replace(spec, members=members)
        spec_problems = check_spec(spec)
        clashes = sorted(item.name for item in walk_specs(spec) if item.name in used)
        if clashes:
            spec_problems.append(f"name {', '.join(clashes)} is already used by another agent")
        if spec_problems:
            problems.append(f"specialist {spec.name} not offered: {'; '.join(spec_problems)}")
        else:
            selected.append(spec)
            used.update(item.name for item in walk_specs(spec))
    return selected, problems
