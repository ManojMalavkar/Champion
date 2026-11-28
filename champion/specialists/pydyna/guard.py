"""Guard of the PyDyna Code sub-agent (T1.2; user, 28 Nov 2026), enforced in code.

The Code sub-agent writes with the core ``file_create`` / ``file_write`` / ``file_edit`` and checks
with ``code_check`` and ``check_pydyna_script``. This module wraps those five tools for that
sub-agent only (same names, same arguments). The core tools still do all the work: access level,
path policy, approval, ``/changes``, ``/undo``, audit. The wrappers add two rules.

1. Output paths. A write or edit is refused (``BLOCKED``, audited) unless the file
   * ends in ``.py`` and its name is letters, digits, ``_`` or ``-`` (starting with a letter or
     digit);
   * would not be imported instead of a library when a script in that folder runs: Python's
     standard modules, ``ansys``, ``pandas``, ``numpy``, ``champion``, and ``conftest``,
     ``setup``, ``sitecustomize``, ``usercustomize``, ``__init__``, ``__main__``;
   * is not inside Champion's own package (so the templates and the guard stay read-only).
   Whether the folder may be written at all stays the core path policy's decision.

2. Correction attempts, per chat session and file. A cycle is a write or edit of the file followed
   by its validation (``check_pydyna_script`` with ``path``, ``code_check``). A cycle fails when a
   validation of the file reports a problem. The first write is the original, not a correction.
   After it, at most ``MAX_FAILED_CORRECTIONS`` (3) failed correction cycles; then every further
   write or edit of that file is ``BLOCKED`` for the session. It stops earlier when there is no
   progress: a correction leaves the code as in an earlier failed cycle (same SHA-256), or a
   validator reports the same problems as in the previous failed cycle. Searches, lookups and
   checks of code text are not counted. A cycle in which both validators pass resets the count.
   The validator result says where the count stands (``correction_attempts``).

Limits: a different file name starts its own count; the count lives in the session's
``ChampionContext`` (a new chat starts at zero).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import champion

MAX_FAILED_CORRECTIONS = 3
VALIDATORS = frozenset({"check_pydyna_script", "code_check"})
PACKAGE_ROOT = Path(champion.__file__).resolve().parent
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.py$")
CHECK_ROW = re.compile(r"^(OK|ERROR)\s+(.+?) \((?:python|json|yaml|toml)\)(?::|$)")  # code_check rows
SHADOWING = frozenset(sys.stdlib_module_names) | {
    "ansys", "pandas", "numpy", "champion", "conftest", "setup", "sitecustomize", "usercustomize",
    "__init__", "__main__"}
_STATE = "_pydyna_code_attempts"  # attribute on the session's ChampionContext
AGENT = "pydyna_code"


@dataclass
class Attempts:
    """Correction cycles of one file in one session."""
    content: str | None = None  # SHA-256 after the last write
    open: bool = False  # a write happened; its validation decides the cycle
    cycles: int = 0  # failed cycles (the first write's included)
    counted: bool = False  # this cycle is already counted as failed
    passed: set[str] = field(default_factory=set)  # validators that passed in this cycle
    failed_contents: set[str] = field(default_factory=set)
    previous: dict[str, str] = field(default_factory=dict)  # validator -> problems, last failed cycle
    current: dict[str, str] = field(default_factory=dict)  # validator -> problems, this cycle
    stopped: str | None = None

    @property
    def corrections(self) -> int:
        return max(self.cycles - 1, 0)

    def wrote(self, digest: str) -> None:
        if self.counted:
            self.previous = dict(self.current)
        self.content, self.open, self.counted = digest, True, False
        self.passed, self.current = set(), {}

    def validated(self, validator: str, ok: bool, problems: str, name: str) -> str | None:
        """Record one validation of the file after a write; the note for the result, if any."""
        if self.stopped:
            return self.stopped
        if ok:
            self.passed.add(validator)
            if not self.counted and VALIDATORS <= self.passed:
                had = self.cycles
                self.cycles, self.failed_contents, self.previous = 0, set(), {}
                return f"{name}: both checks pass" + ("; correction count reset" if had else "")
            return None
        self.current[validator] = problems
        repeated_code = False
        if not self.counted:
            self.counted, self.cycles = True, self.cycles + 1
            repeated_code = self.content in self.failed_contents
            self.failed_contents.add(self.content)
        if self.cycles == 1:
            return (f"{name}: the first version has problems; fix them with file_edit (exact old_text -> new_text), "
                    f"at most {MAX_FAILED_CORRECTIONS} correction attempts")
        if repeated_code:
            return self.stop(f"{name}: no progress, the correction left the code as in an earlier failed version")
        if self.previous.get(validator) == problems:
            return self.stop(f"{name}: no progress, {validator} reports the same problems as before the correction")
        if self.corrections >= MAX_FAILED_CORRECTIONS:
            return self.stop(f"{name}: {MAX_FAILED_CORRECTIONS} correction attempts failed")
        return (f"{name}: correction {self.corrections} of {MAX_FAILED_CORRECTIONS} failed; fix with file_edit "
                "(exact old_text -> new_text)")

    def stop(self, reason: str) -> str:
        self.stopped = (f"STOP: {reason}. Do not change this file again in this session; report the remaining "
                        "issues with their lines to the user.")
        return self.stopped


def attempts(context: Any) -> dict[str, Attempts]:
    state = getattr(context, _STATE, None)
    if state is None:
        state = {}
        setattr(context, _STATE, state)
    return state


def target(context: Any, path: str) -> Path:
    """The file a relative or absolute path names (as the core tools resolve it)."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(context.working_directory) / candidate
    return Path(os.path.realpath(candidate))


def path_problem(file: Path) -> str | None:
    """Why the Code sub-agent may not write this file, or None."""
    if file.suffix != ".py":
        return f"{file.name}: the PyDyna Code agent writes only .py scripts"
    if not NAME.match(file.name):
        return f"{file.name}: a script name has letters, digits, _ or - only and ends in .py"
    if file.stem.lower() in SHADOWING:
        return f"{file.name} would be imported instead of the module {file.stem}; choose another name"
    if file == PACKAGE_ROOT or PACKAGE_ROOT in file.parents:
        return f"{file} is inside Champion's own code (templates and tools are read-only)"
    return None


def _digest(file: Path) -> str | None:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return None


def _deny(context: Any, tool: str, path: str, reason: str) -> str:
    audit = getattr(context, "audit", None)
    if audit is not None:
        audit.record(tool=tool, arguments={"path": path}, access_level=context.level.name, decision="deny",
                     reason=f"pydyna guard: {reason}", agent=AGENT)
    return f"BLOCKED: {reason}"


def _arguments(raw: str) -> dict | None:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def write_tool(tool: Any) -> Any:
    """``tool`` (file_create / file_write / file_edit) with the path rules and the attempt stop."""
    async def invoke(ctx: Any, raw: str) -> Any:
        context, args = ctx.context, _arguments(raw)
        path = args.get("path") if args else None
        if not isinstance(path, str) or not path.strip():
            return await tool.on_invoke_tool(ctx, raw)  # the core tool reports the bad arguments
        file = target(context, path)
        problem = path_problem(file)
        if problem:
            return _deny(context, tool.name, path, problem)
        state = attempts(context).get(str(file))
        if state is not None and state.stopped:
            return _deny(context, tool.name, path, state.stopped)
        before = _digest(file)
        result = await tool.on_invoke_tool(ctx, raw)
        after = _digest(file)
        if after is not None and after != before:  # written (not refused, not a dry run)
            attempts(context).setdefault(str(file), Attempts()).wrote(after)
        return result

    return dataclasses.replace(tool, on_invoke_tool=invoke)


def _pydyna_result(context: Any, args: dict, result: str) -> str:
    path = args.get("path")
    if not isinstance(path, str):
        return result  # code text: not a cycle
    file = target(context, path)
    state = attempts(context).get(str(file))
    if state is None or not state.open:
        return result
    try:
        data = json.loads(result)
    except ValueError:
        return result  # ERROR / BLOCKED: no validation happened
    if not isinstance(data, dict) or "ok" not in data:
        return result
    issues = [[i.get("line"), i.get("code"), i.get("message")] for i in data.get("issues", []) if isinstance(i, dict)]
    problems = json.dumps(sorted(issues, key=json.dumps))  # a line may be None
    note = state.validated("check_pydyna_script", bool(data["ok"]), problems, file.name)
    if note:
        data["correction_attempts"] = note
    return json.dumps(data, ensure_ascii=False, indent=1)


def _code_check_result(context: Any, args: dict, result: str) -> str:
    paths = args.get("paths")
    if not isinstance(paths, list) or not isinstance(result, str):
        return result
    rows = {}
    for line in result.splitlines():
        match = CHECK_ROW.match(line)
        if match:
            rows[match.group(2)] = line  # the path may contain spaces
    notes = []
    for path in paths:
        if not isinstance(path, str):
            continue
        file = target(context, path)
        state = attempts(context).get(str(file))
        row = rows.get(str(file))
        if state is None or not state.open or row is None:
            continue
        note = state.validated("code_check", row.startswith("OK"), row, file.name)
        if note:
            notes.append(note)
    return result + "".join(f"\ncorrection_attempts: {note}" for note in notes)


def validator_tool(tool: Any) -> Any:
    """``tool`` (check_pydyna_script / code_check) that also records the cycle of a written file."""
    if tool.name not in VALIDATORS:
        raise ValueError(f"{tool.name} is not a validator ({', '.join(sorted(VALIDATORS))})")

    async def invoke(ctx: Any, raw: str) -> Any:
        result = await tool.on_invoke_tool(ctx, raw)
        args = _arguments(raw)
        if args is None or not isinstance(result, str):
            return result
        if tool.name == "check_pydyna_script":
            return _pydyna_result(ctx.context, args, result)
        return _code_check_result(ctx.context, args, result)

    return dataclasses.replace(tool, on_invoke_tool=invoke)
