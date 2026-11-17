"""Specialist registry: which specialists exist and which are offered (step 3).

Specialists register themselves (``register_specialist``) when their package
is imported. ``select_specialists`` decides, for one run of Champion, which
ones the orchestrator gets:

* switched on in the config (``agents.<name>.enabled``, default from the spec);
* the spec passes ``check_spec``. A spec with a problem, for example a tool
  without a tool-policy entry, is not offered at all (deny by default) and the
  problem is reported so the chat can show it.
"""

from __future__ import annotations

from champion.core.config import Config
from champion.policy.tool_policy import has_rule
from champion.specialists.base import NAME_PATTERN, SpecialistSpec

# Tools of the orchestrator itself; a specialist tool must not reuse these names.
RESERVED_TOOL_NAMES = frozenset({"session_info", "filesystem_list", "filesystem_read", "filesystem_disk_usage"})

_SPECS: dict[str, SpecialistSpec] = {}


def register_specialist(spec: SpecialistSpec) -> SpecialistSpec:
    """Add a specialist. Registering the same name with a different spec is an error."""
    existing = _SPECS.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(f"specialist {spec.name} is already registered with a different spec")
    _SPECS[spec.name] = spec
    return spec


def unregister_specialist(name: str) -> None:
    _SPECS.pop(name, None)


def registered_specialists() -> list[SpecialistSpec]:
    return [_SPECS[name] for name in sorted(_SPECS)]


def load_builtin_specialists() -> None:
    """Import the specialists shipped with Champion so they register themselves.

    None yet: the Linux specialist comes in step 4, the PyDyna specialist in step 5.
    """


def check_spec(spec: SpecialistSpec) -> list[str]:
    """Return the problems that stop a spec from being offered (empty list = fine)."""
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
    return problems


def select_specialists(config: Config, specs: list[SpecialistSpec] | None = None
                       ) -> tuple[list[SpecialistSpec], list[str]]:
    """Specialists to offer and the problems of those left out (for display)."""
    if specs is None:
        load_builtin_specialists()
        specs = registered_specialists()
    selected, problems = [], []
    for spec in specs:
        if not config.get(f"agents.{spec.name}.enabled", spec.enabled_by_default):
            continue
        spec_problems = check_spec(spec)
        if spec_problems:
            problems.append(f"specialist {spec.name} not offered: {'; '.join(spec_problems)}")
        else:
            selected.append(spec)
    return selected, problems
