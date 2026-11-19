"""Tool-policy registry: every tool must declare what it does before it can run.

Deny by default: a tool without an entry here is refused by the tool guard
(``tools/guard.py``), and from step 3 a specialist whose tool has no entry is
not offered to the orchestrator. The same registry will hold the entries for
MCP tools (step 5) and agent tools on the agent branches.

Fields:
* ``right``: the access the tool needs: ``none`` (no files), ``read``,
  ``edit`` or ``execute`` (steps 7-8). ``write`` is accepted as the old
  name of ``edit`` (step 4e).
* ``approval``: ``never`` or ``always`` (the user must approve each call;
  used from step 6).
* ``sends_data_out``: the tool sends data to a service other than the model
  provider (web search, mail; step 5 and later).
"""

from __future__ import annotations

from dataclasses import dataclass

from champion.policy.scope import PolicyDenied

RIGHTS = ("none", "read", "edit", "execute")
APPROVALS = ("never", "always")


@dataclass(frozen=True)
class ToolRule:
    name: str
    right: str
    summary: str
    approval: str = "never"
    sends_data_out: bool = False

    def __post_init__(self) -> None:
        if self.right == "write":
            object.__setattr__(self, "right", "edit")
        if self.right not in RIGHTS:
            raise ValueError(f"{self.name}: right must be one of {RIGHTS}")
        if self.approval not in APPROVALS:
            raise ValueError(f"{self.name}: approval must be one of {APPROVALS}")


_RULES: dict[str, ToolRule] = {}


def register(rule: ToolRule) -> ToolRule:
    """Add a rule. Registering the same name with different content is an error."""
    existing = _RULES.get(rule.name)
    if existing is not None and existing != rule:
        raise ValueError(f"tool {rule.name} already has a different policy entry")
    _RULES[rule.name] = rule
    return rule


def rule_for(name: str) -> ToolRule:
    try:
        return _RULES[name]
    except KeyError:
        raise PolicyDenied(f"tool {name} has no policy entry, so it is not allowed to run") from None


def has_rule(name: str) -> bool:
    return name in _RULES


def all_rules() -> list[ToolRule]:
    return sorted(_RULES.values(), key=lambda r: r.name)
