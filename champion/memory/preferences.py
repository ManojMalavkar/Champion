"""L3 - user and operating preferences (track T8.4).

Stable interaction and execution preferences, never CAE conclusions. One file
per user: ``~/.champion/memory/preferences.json``::

    {"user_id": "manoj",
     "preferences": {"unit_system": {"value": "mm-ms-kg-kN", "source": "user", "set_at": "..."}}}

Rules:
* Only known keys (``SPECS``), each with a type and, where useful, choices.
* Who saves: the user, or the agent after the user confirmed its proposal
  (source ``agent_confirmed``); the agent alone cannot set a preference.
* Engineering facts, requirements and simulation results are refused here
  (``separation.py``): "Show force in kN" is a preference, "Section force
  must remain below 320 kN" is not.
* Preferences can only make Champion stricter: confirmation before
  destructive actions cannot be switched off, generated code is never run
  automatically, and project roots listed here do not grant access (the
  access rules and ``/access`` do).
* Secrets are refused.
"""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from champion.memory.separation import LAYER_TEXT, engineering_layer
from champion.memory.storage import MemoryRefused, clean_text, now_iso, read_json, user_memory_root, write_json

SOURCES = ("user", "agent_confirmed")


@dataclass(frozen=True)
class PreferenceSpec:
    key: str
    kind: str  # text, choice, bool, paths, map
    help: str
    choices: tuple[str, ...] = ()
    pattern: str = ""
    safety: Callable[[Any], str | None] | None = None  # reason the value would weaken safety


def _must_confirm(value: Any) -> str | None:
    return None if value is True else "Champion always asks before destructive actions; a preference cannot turn that off"


SPECS = {spec.key: spec for spec in (
    PreferenceSpec("unit_system", "text", "consistent unit system, e.g. mm-ms-kg-kN",
                   pattern=r"^[A-Za-z]+(-[A-Za-z]+){3}$"),
    PreferenceSpec("display_units", "map", "unit per quantity for answers, e.g. force: kN"),
    PreferenceSpec("result_format", "choice", "default result format", choices=("table", "csv", "json", "plot", "text")),
    PreferenceSpec("report_format", "choice", "default report format", choices=("pptx", "pdf", "html", "md", "xlsx")),
    PreferenceSpec("report_configuration", "text", "preferred report configuration (name or path)"),
    PreferenceSpec("default_solver", "text", "preferred solver, e.g. LS-DYNA"),
    PreferenceSpec("solver_executable", "text", "preferred solver executable (name or path)"),
    PreferenceSpec("default_precision", "choice", "solver build",
                   choices=("SMP-single", "SMP-double", "MPP-single", "MPP-double")),
    PreferenceSpec("working_locations", "paths", "usual working folders"),
    PreferenceSpec("hpc_gateway", "text", "default HPC gateway host"),
    PreferenceSpec("output_naming", "text", "output naming pattern"),
    PreferenceSpec("destructive_action_confirmation", "bool", "ask before destructive actions (always true)",
                   safety=_must_confirm),
    PreferenceSpec("generated_code_policy", "choice", "what happens with generated code",
                   choices=("write_but_do_not_run", "ask_before_run")),
    PreferenceSpec("allowed_project_roots", "paths", "project roots you use (a hint only; access is set with /access)"),
    PreferenceSpec("tool_aliases", "map", "your names for tools or commands (commands still follow the rules)"),
)}


def preferences_path(root: str | os.PathLike | None = None) -> Path:
    return Path(root or user_memory_root()) / "preferences.json"


def _check_text(key: str, value: Any, max_chars: int = 500) -> str:
    text = clean_text(value, key, max_chars, required=True)
    layer = engineering_layer(text)
    if layer:
        raise MemoryRefused(f"'{text}' is {LAYER_TEXT[layer]}, not a preference; it is not saved as one")
    return text


def check_value(key: str, value: Any) -> Any:
    """The checked value for ``key``; refused with a reason otherwise."""
    spec = SPECS.get(key)
    if spec is None:
        raise MemoryRefused(f"unknown preference {key!r}; known: {', '.join(sorted(SPECS))}")
    if spec.kind == "bool":
        if isinstance(value, str) and value.strip().lower() in ("true", "yes", "on", "false", "no", "off"):
            value = value.strip().lower() in ("true", "yes", "on")
        if not isinstance(value, bool):
            raise MemoryRefused(f"{key} is true or false")
    elif spec.kind == "choice":
        text = _check_text(key, value)
        match = {choice.lower(): choice for choice in spec.choices}.get(text.lower())
        if match is None:
            raise MemoryRefused(f"{key} is one of {', '.join(spec.choices)}")
        value = match
    elif spec.kind == "text":
        value = _check_text(key, value)
        if spec.pattern and not re.match(spec.pattern, value):
            raise MemoryRefused(f"{key}: {spec.help}")
    elif spec.kind == "paths":
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, (list, tuple)) or not items or len(items) > 50:
            raise MemoryRefused(f"{key} is a list of 1 to 50 folders")
        value = []
        for item in items:
            path = _check_text(key, item, 1000)
            if not (path.startswith("/") or path.startswith("~/") or path == "~"):
                raise MemoryRefused(f"{key}: '{path}' is not an absolute path (start with / or ~/)")
            if path not in value:
                value.append(path)
    elif spec.kind == "map":
        if not isinstance(value, dict) or not value or len(value) > 50:
            raise MemoryRefused(f"{key} is a mapping with 1 to 50 entries")
        value = {_check_text(key, name, 100): _check_text(key, item, 500) for name, item in value.items()}
    if spec.safety is not None:
        reason = spec.safety(value)
        if reason:
            raise MemoryRefused(reason)
    return value


class PreferenceStore:
    """The preferences of one user (``preferences.json``)."""

    def __init__(self, path: str | os.PathLike | None = None, *, user_id: str | None = None) -> None:
        self.path = Path(path) if path is not None else preferences_path()
        self.user_id = user_id or getpass.getuser()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"user_id": self.user_id, "preferences": {}}
        data = read_json(self.path)
        if not isinstance(data, dict) or not isinstance(data.get("preferences"), dict):
            raise MemoryRefused(f"{self.path.name} is not a preferences file")
        return data

    def set(self, key: str, value: Any, *, source: str = "user") -> Any:
        if source not in SOURCES:
            raise MemoryRefused("a preference is set by the user, or by the agent after the user confirmed it "
                                "(source 'agent_confirmed')")
        value = check_value(key, value)
        data = self._load()
        data["preferences"][key] = {"value": value, "source": source, "set_at": now_iso()}
        write_json(self.path, data)
        return value

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._load()["preferences"].get(key)
        return default if entry is None else entry["value"]

    def entry(self, key: str) -> dict | None:
        return self._load()["preferences"].get(key)

    def forget(self, key: str) -> bool:
        data = self._load()
        if key not in data["preferences"]:
            return False
        del data["preferences"][key]
        write_json(self.path, data)
        return True

    def all(self) -> dict:
        """``{"user_id": ..., "preferences": {key: value}}`` (the user's example format)."""
        data = self._load()
        return {"user_id": data.get("user_id", self.user_id),
                "preferences": {key: entry["value"] for key, entry in sorted(data["preferences"].items())}}

    def render(self, max_chars: int = 2000) -> str:
        """Text block for the agent instructions (M6); empty when nothing is set."""
        prefs = self.all()["preferences"]
        if not prefs:
            return ""
        lines = [f"- {key}: {value}" for key, value in prefs.items()]
        text = ("[User preferences: how to present and work; not engineering facts, "
                "and they never change the access rules or approvals]\n" + "\n".join(lines))
        return text if len(text) <= max_chars else text[:max_chars - 40].rstrip() + "\n- [more not shown]"
