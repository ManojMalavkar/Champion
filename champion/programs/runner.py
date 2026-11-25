"""Running a trusted program for an agent (M2.4).

Order of one call:

1. Arguments: every value is checked against its parameter (type, range, pattern,
   enum); a value starting with ``-`` is refused unless it comes from an enum (no extra
   options can be slipped in); every path goes through ``check_path`` with its declared
   access, exactly like the file tools. Shell characters are plain text: nothing is
   ever given to a shell.
2. Access: local edit access is needed only for the paths a program may change (its
   path parameters with ``access: edit``); every other program needs read. ``remote``
   is a separate permission: the user approves every remote run, at any level.
3. Status: the program must be trusted now (file, resolved interpreter and entry
   hashed again).
4. Approval: ``delete``, ``remote`` and ``approval: always`` ask the user every time;
   the request shows the program, its SHA-256, the full command, the folder and the
   paths, and its fingerprint includes the hash.
5. Just before the start the hashes are taken once more; a change refuses the run.
6. Run with the 4a executor: no shell, clean environment plus the declared names and
   values (secrets never), declared ``path_prepend``, time/CPU/memory/file limits,
   process-group kill, output cap, private keys and tokens hidden.
7. One change-list entry per edit path, and the audit row of the tool call gets the program, hash,
   command, class, exit code and environment names.

Changes made by an external program are audited (change list, audit row) but cannot
be undone: Champion does not know what the program changed inside a path. Undo of
program changes needs the staging of M3; until then every result says so.
"""

from __future__ import annotations

import math
import os
import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from champion.core.context import ChampionContext
from champion.execution.approval import ApprovalRequest, ask
from champion.execution.commands import Limits, run_steps
from champion.policy.scope import PolicyDenied, check_path
from champion.programs.manifest import TRUSTED, state_of
from champion.programs.spec import MAX_STRING, TOKEN, Param, ProgramSpec, env_name_problem
from champion.tools import changes
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError

CONTROL = re.compile(r"[\x00-\x1f\x7f]")
NO_UNDO = ("changes made by an external program are listed in /changes and the audit log but cannot be "
           "undone (undo of program changes needs the staging of M3)")
REMOTE_NOTE = "remote actions are audited but cannot be undone by Champion"


def _value(context: ChampionContext, param: Param, value: Any) -> Any:
    """The checked value (a path becomes its absolute, checked form)."""
    label = f"argument {param.name}"
    if param.type == "path":
        if not isinstance(value, str):
            raise ToolError(f"{label} must be a path")
        return check_path(context.level, context.working_directory, value, param.access)
    if param.type in ("int", "float"):
        ok = isinstance(value, int) if param.type == "int" else isinstance(value, (int, float))
        if isinstance(value, bool) or not ok or (isinstance(value, float) and not math.isfinite(value)):
            raise ToolError(f"{label} must be a {'whole number' if param.type == 'int' else 'number'}")
        if param.minimum is not None and value < param.minimum:
            raise ToolError(f"{label} must be at least {param.minimum}")
        if param.maximum is not None and value > param.maximum:
            raise ToolError(f"{label} must be at most {param.maximum}")
        if value < 0 and param.minimum is None:
            raise ToolError(f"{label} cannot be negative (it would look like an option)")
        return value
    if param.type == "bool":
        if not isinstance(value, bool):
            raise ToolError(f"{label} must be true or false")
        return value
    if param.type == "enum":
        if value not in param.values:
            raise ToolError(f"{label} must be one of {', '.join(param.values)}")
        return value
    if not isinstance(value, str) or not value or len(value) > MAX_STRING:
        raise ToolError(f"{label} must be a text of 1-{MAX_STRING} characters")
    if CONTROL.search(value):
        raise ToolError(f"{label} contains a control character")
    if value.startswith("-"):
        raise ToolError(f"{label} cannot start with '-' (extra options are not allowed)")
    if not re.fullmatch(param.pattern or "", value):
        raise ToolError(f"{label} {value!r} does not match the allowed form {param.pattern}")
    return value


def build_command(context: ChampionContext, spec: ProgramSpec, arguments: Mapping[str, Any]
                  ) -> tuple[list[str], Path, list[Path], dict[str, Any]]:
    """(tokens after the program, folder to run in, edit paths, checked values)."""
    if not isinstance(arguments, Mapping):
        raise ToolError("the arguments must be an object")
    unknown = sorted(set(arguments) - {p.name for p in spec.params})
    if unknown:
        raise ToolError(f"unknown argument(s) {', '.join(unknown)} for {spec.tool_name}")
    values: dict[str, Any] = {}
    for param in spec.params:
        value = arguments.get(param.name)
        if value is None:
            if param.required:
                raise ToolError(f"argument {param.name} is required")
            value = param.default
        values[param.name] = None if value is None else _value(context, param, value)
    tokens: list[str] = []
    for token in spec.args:
        match = TOKEN.match(token)
        if not match:
            tokens.append(token)
            continue
        param, value = spec.param(match.group(1)), values[match.group(1)]
        if value is None:
            continue
        if param.type == "bool":
            if value:
                tokens.append(param.flag)
            continue
        tokens.append(str(value))
    if spec.cwd:
        folder = values[TOKEN.match(spec.cwd).group(1)]
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder (the program runs in it)")
    else:
        folder = check_path(context.level, context.working_directory, ".", "read")
    edits = [values[p.name] for p in spec.params if p.type == "path" and p.access == "edit" and values[p.name]]
    return tokens, folder, edits, values


def _limits(context: ChampionContext, spec: ProgramSpec) -> Limits:
    config = context.config
    seconds = min(spec.timeout_seconds, int(config.get("programs.max_timeout_seconds", 3600)))
    file_bytes = spec.max_file_bytes or int(config.get("execution.max_command_file_bytes"))
    return Limits(timeout_seconds=float(seconds), cpu_seconds=int(seconds),
                  memory_bytes=spec.max_memory_bytes or int(config.get("execution.max_memory_bytes")),
                  file_bytes=min(file_bytes, int(config.get("files.max_model_bytes"))),
                  output_chars=int(config.get("execution.max_output_chars")))


def _environment(spec: ProgramSpec) -> tuple[dict[str, str], list[str]]:
    extra: dict[str, str] = {}
    for name in spec.env_pass:
        if env_name_problem(name) is None and name in os.environ:
            extra[name] = os.environ[name]
    for name, value in spec.env_set:
        if env_name_problem(name) is None:
            extra[name] = value
    missing = [n for n in spec.env_pass if n not in os.environ]
    return extra, missing


def _report(spec: ProgramSpec, shown: list[str], result: Any, limits: Limits, missing: list[str]) -> str:
    lines = [f"$ {shlex.join(shown)}", f"program {spec.name}: exit code {result.exit_code}, {result.seconds:.1f} s"]
    if missing:
        lines.append(f"[not set in your environment, so not passed: {', '.join(missing)}]")
    if result.stdout:
        lines.append(result.stdout.rstrip("\n"))
    if result.stderr:
        lines += ["[stderr]", result.stderr.rstrip("\n")]
    if result.truncated:
        lines.append(f"[output cut: {human_size(result.output_bytes)} produced, first {limits.output_chars} "
                     "characters shown]")
    if result.timed_out:
        lines.append(f"[stopped at the time limit of {limits.timeout_seconds:.0f} s]")
    if result.redacted:
        lines.append("[private keys or tokens in the output were hidden by Champion]")
    return "\n".join(lines)


def run_program(context: ChampionContext, spec: ProgramSpec, arguments: Mapping[str, Any],
                audit: dict | None = None, agent: str = "") -> str:
    audit = audit if audit is not None else {}
    if spec.right == "edit" and not context.level.allows("edit"):
        names = ", ".join(p.name for p in spec.edit_params)
        raise PolicyDenied(f"access level {context.level.name} does not allow editing, and program {spec.name} "
                           f"changes the paths given as {names}")
    tokens, folder, edits, _ = build_command(context, spec, arguments)
    state = state_of(spec, context.config, context.working_directory)
    if state.status != TRUSTED:
        raise PolicyDenied(f"program {spec.name} is {state.status}: {state.detail}; it was not run")
    shown = [state.path, *tokens]
    audit.update({"program": spec.name, "source": spec.source, "sha256": state.file_sha256, "class": spec.cls,
                  "argv": shown})
    if spec.asks:
        note = (f"runs the trusted program '{spec.name}' ({spec.source}), SHA-256 {state.file_sha256[:16]}; "
                f"{spec.description[:200]}")
        if edits:
            note += f"; {NO_UNDO}"
        elif spec.cls == "remote":
            note += f"; {REMOTE_NOTE}"
        paths = tuple(str(p) for p in edits) or (str(folder),)
        ask(context, ApprovalRequest(spec.tool_name, state.executable, tuple(shown), spec.cls, str(folder),
                                     context.level.name, paths, note=note))
    again = state_of(spec, context.config, context.working_directory)
    pinned = ("file_sha256", "interpreter_requested", "interpreter_resolved", "interpreter_sha256", "argv0")
    if again.status != TRUSTED or any(getattr(again, k) != getattr(state, k) for k in pinned):
        raise PolicyDenied(f"program {spec.name} changed just before the start; it was not run")
    limits = _limits(context, spec)
    extra, missing = _environment(spec)
    audit["env"] = sorted(extra)  # names only, never values
    result = run_steps([[*again.argv0, *tokens]], str(folder), limits, extra_env=extra,
                       executables=[again.executable], path_prepend=spec.path_prepend)
    audit.update({"exit_code": result.exit_code, "timed_out": result.timed_out})
    if edits:
        group = changes.new_group()
        for path in edits:
            action = "deleted" if spec.cls == "delete" else "modified"  # audit.ACTIONS vocabulary
            changes.record(context, tool=spec.tool_name, action=action, path=path, agent=agent,
                           detail=f"program {spec.name} ({spec.cls}), exit code {result.exit_code}; no undo "
                                  "(M3 staging)", group_id=group)
    text = _report(spec, shown, result, limits, missing)
    if edits:
        text += f"\n[note: {NO_UNDO}]"
    elif spec.cls == "remote":
        text += f"\n[note: {REMOTE_NOTE}]"
    return text
