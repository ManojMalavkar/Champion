"""How a trusted program is declared (M2.2).

One entry describes one tool ``program_<name>``: which file runs, with which arguments,
for which agent, how risky it is and in which environment. Entries come from the
user's manifest (``programs.yaml``) or from a track package (``register_program``);
both are parsed and checked here with the same rules.

```yaml
programs:
  - name: run_summary
    program: run_summary.sh            # relative = inside programs.folder; or absolute;
                                       # or config:<section>.<key> (path from the config)
    description: Counts keywords and files of an LS-DYNA run folder
    agent: linux_coder
    class: read                        # read | edit | delete | remote
    args: ["--folder", "{run_dir}", "{verbose}"]
    params:
      run_dir: {type: path, access: read, required: true, description: run folder}
      verbose: {type: bool, flag: "-v"}
    timeout_seconds: 60
```

Argument template: each token is a literal or exactly ``{param}``. A missing optional
value drops its token; a bool gives its ``flag`` or nothing. Types: ``path`` (``access``
read or edit), ``int`` / ``float`` (``min`` / ``max``), ``bool`` (``flag``), ``enum``
(``values``), ``string`` (``pattern``, at most 200 characters). Every path parameter
with edit access is a path the program changes (shown in ``/changes``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from champion.core.config import CHECK_ENV_FIXED, SECRET_NAME

NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TOKEN = re.compile(r"^\{([a-z][a-z0-9_]{0,30})\}$")
DEFAULT_PATTERN = r"[A-Za-z0-9._,:=+-]{1,200}"
CLASSES = ("read", "edit", "delete", "remote")
TYPES = ("path", "int", "float", "bool", "enum", "string")
APPROVALS = ("auto", "always")
MAX_STRING = 200
MAX_TIMEOUT = 86400
ENTRY_KEYS = {"name", "program", "description", "agent", "class", "args", "params", "cwd", "env_pass", "env_set",
              "path_prepend", "interpreter", "timeout_seconds", "max_memory_bytes", "max_file_bytes", "approval"}
PARAM_KEYS = {"type", "description", "required", "default", "access", "min", "max", "values", "pattern", "flag"}


class SpecError(ValueError):
    """An entry that cannot be used; ``problems`` lists every reason."""

    def __init__(self, name: str, problems: list[str]) -> None:
        self.name, self.problems = name, problems
        super().__init__(f"program {name}: " + "; ".join(problems))


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str = ""
    required: bool = False
    default: Any = None
    access: str | None = None  # path: read | edit
    minimum: float | None = None
    maximum: float | None = None
    values: tuple[str, ...] = ()  # enum
    pattern: str | None = None  # string
    flag: str | None = None  # bool

    def schema(self) -> dict[str, Any]:
        base = {"path": "string", "string": "string", "enum": "string", "int": "integer", "float": "number",
                "bool": "boolean"}[self.type]
        item: dict[str, Any] = {"type": base if self.required else [base, "null"]}
        if self.type == "enum":
            item["enum"] = list(self.values) + ([] if self.required else [None])
        text = self.description or self.name
        if self.type == "path":
            text += f" (path; {self.access} access needed)"
        if self.minimum is not None or self.maximum is not None:
            text += f" (from {self.minimum if self.minimum is not None else '-'} to "
            text += f"{self.maximum if self.maximum is not None else '-'})"
        if not self.required:
            text += f"; null = {'default ' + repr(self.default) if self.default is not None else 'not given'}"
        item["description"] = text
        return item


@dataclass(frozen=True)
class ProgramSpec:
    name: str
    program: str  # absolute path, path relative to programs.folder, or config:<section>.<key>
    description: str
    agent: str
    cls: str
    args: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    cwd: str | None = None  # None = working folder; "{param}" = that path parameter (a folder)
    env_pass: tuple[str, ...] = ()
    env_set: tuple[tuple[str, str], ...] = ()
    path_prepend: tuple[str, ...] = ()
    interpreter: str | None = None  # absolute path; None = from the file's #! line, or a binary
    timeout_seconds: int = 300
    max_memory_bytes: int | None = None
    max_file_bytes: int | None = None
    approval: str = "auto"
    source: str = "manifest"  # "manifest" or "track:<package>"

    @property
    def tool_name(self) -> str:
        return f"program_{self.name}"

    def param(self, name: str) -> Param:
        return next(p for p in self.params if p.name == name)

    @property
    def asks(self) -> bool:
        """True when every run needs the user's yes."""
        return self.cls in ("delete", "remote") or self.approval == "always"

    @property
    def edit_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if p.type == "path" and p.access == "edit")

    @property
    def right(self) -> str:
        """Right in the tool policy: edit only when the program takes paths it may change.

        Remote is a separate permission (approval of every run), not local edit access: a
        remote program without edit paths runs at a read level."""
        return "edit" if self.edit_params else "read"

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the whole entry, pinned by trust: program path, class, agent, typed
        parameters, argument template, working folder, environment (pass, set, PATH),
        interpreter, timeout and limits, approval policy and where it came from."""
        data = asdict(self)
        text = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {p.name: p.schema() for p in self.params},
                "required": [p.name for p in self.params], "additionalProperties": False}


def env_name_problem(name: Any) -> str | None:
    if not isinstance(name, str) or not ENV_NAME.match(name):
        return f"{name!r} is not an environment variable name"
    if name.upper() in CHECK_ENV_FIXED:
        return f"{name} is set by Champion and cannot be changed"
    if re.search(SECRET_NAME, name, re.IGNORECASE):
        return f"{name} looks like a secret; secrets are never passed to programs"
    return None


def _number(value: Any, kind: str) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) if kind == "int" else isinstance(value, (int, float))


def _parse_param(name: str, raw: Any, problems: list[str]) -> Param | None:
    where = f"params.{name}"
    if not PARAM_NAME.match(str(name)):
        problems.append(f"{where}: name must be lowercase letters, digits or _ (at most 31)")
        return None
    if not isinstance(raw, Mapping):
        problems.append(f"{where} must be a mapping (type: ...)")
        return None
    unknown = sorted(set(raw) - PARAM_KEYS)
    if unknown:
        problems.append(f"{where}: unknown key(s) {', '.join(unknown)}")
    kind = raw.get("type")
    if kind not in TYPES:
        problems.append(f"{where}.type must be one of {', '.join(TYPES)} (got {kind!r})")
        return None
    required = raw.get("required", False)
    if not isinstance(required, bool):
        problems.append(f"{where}.required must be true or false")
        required = False
    default = raw.get("default")
    access, minimum, maximum, values, pattern, flag = None, raw.get("min"), raw.get("max"), (), None, None
    allowed = {"path": {"access"}, "int": {"min", "max"}, "float": {"min", "max"}, "bool": {"flag"},
               "enum": {"values"}, "string": {"pattern"}}[kind]
    wrong = sorted((set(raw) & {"access", "min", "max", "values", "pattern", "flag"}) - allowed)
    if wrong:
        problems.append(f"{where}: {', '.join(wrong)} not used with type {kind}")
    if kind == "path":
        access = raw.get("access", "read")
        if access == "write":
            access = "edit"
        if access not in ("read", "edit"):
            problems.append(f"{where}.access must be read or edit")
        if default is not None:
            problems.append(f"{where}: a path cannot have a default (the agent gives it)")
    elif kind in ("int", "float"):
        for label, value in (("min", minimum), ("max", maximum)):
            if value is not None and not _number(value, kind):
                problems.append(f"{where}.{label} must be a number of type {kind}")
        if _number(minimum, kind) and _number(maximum, kind) and minimum > maximum:
            problems.append(f"{where}: min is larger than max")
        if default is not None and not _number(default, kind):
            problems.append(f"{where}.default must be of type {kind}")
    elif kind == "bool":
        flag = raw.get("flag")
        if not (isinstance(flag, str) and re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9_.=-]{0,60}", flag)):
            problems.append(f"{where}.flag must be an option like -v or --keep-d3plot")
        if default is not None and not isinstance(default, bool):
            problems.append(f"{where}.default must be true or false")
    elif kind == "enum":
        raw_values = raw.get("values")
        if not (isinstance(raw_values, list) and raw_values and all(isinstance(v, str) and v and "\x00" not in v
                                                                    and len(v) <= MAX_STRING for v in raw_values)):
            problems.append(f"{where}.values must be a list of 1 or more texts")
        else:
            values = tuple(raw_values)
            if len(set(values)) != len(values):
                problems.append(f"{where}.values has a value twice")
            if default is not None and default not in values:
                problems.append(f"{where}.default is not one of the values")
    else:
        pattern = raw.get("pattern", DEFAULT_PATTERN)
        try:
            re.compile(pattern)
        except (re.error, TypeError):
            problems.append(f"{where}.pattern is not a valid regular expression")
        if default is not None and not isinstance(default, str):
            problems.append(f"{where}.default must be a text")
    if required and default is not None:
        problems.append(f"{where}: a required parameter has no default")
    description = raw.get("description", "")
    if not isinstance(description, str):
        problems.append(f"{where}.description must be a text")
        description = ""
    return Param(name=name, type=kind, description=description.strip(), required=required, default=default,
                 access=access, minimum=minimum, maximum=maximum, values=values, pattern=pattern, flag=flag)


def _texts(raw: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        problems.append(f"{where} must be a list of texts")
        return ()
    return tuple(raw)


def parse_entry(raw: Any, source: str = "manifest") -> ProgramSpec:
    """Check one entry; raise ``SpecError`` with every problem found."""
    if not isinstance(raw, Mapping):
        raise SpecError("?", ["an entry must be a mapping (name: ..., program: ...)"])
    name = raw.get("name")
    label = name if isinstance(name, str) else "?"
    problems: list[str] = []
    unknown = sorted(set(raw) - ENTRY_KEYS)
    if unknown:
        problems.append(f"unknown key(s) {', '.join(map(str, unknown))}")
    if not (isinstance(name, str) and NAME.match(name)):
        problems.append("name must be 2-41 lowercase letters, digits or _ and start with a letter")
    program = raw.get("program")
    if not (isinstance(program, str) and program.strip() and "\x00" not in program):
        problems.append("program must be a path (or config:<section>.<key>)")
        program = ""
    elif program.startswith("config:") and not re.fullmatch(r"config:[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", program):
        problems.append("program config:<section>.<key> must name one config key")
    description = raw.get("description")
    if not (isinstance(description, str) and description.strip()) or len(str(description)) > 1000:
        problems.append("description must be a text of 1-1000 characters (it tells the agent what the program does)")
        description = ""
    agent = raw.get("agent")
    if not (isinstance(agent, str) and AGENT_NAME.match(agent)):
        problems.append("agent must be the name of a Champion agent, for example linux_coder")
    cls = raw.get("class")
    if cls == "write":
        cls = "edit"
    if cls not in CLASSES:
        problems.append(f"class must be one of {', '.join(CLASSES)} (got {cls!r})")
    params_raw = raw.get("params") or {}
    params: list[Param] = []
    if not isinstance(params_raw, Mapping):
        problems.append("params must be a mapping of name: {type: ...}")
    else:
        for pname, praw in params_raw.items():
            param = _parse_param(str(pname), praw, problems)
            if param is not None:
                params.append(param)
    by_name = {p.name: p for p in params}
    args = _texts(raw.get("args"), "args", problems)
    used: list[str] = []
    for token in args:
        match = TOKEN.match(token)
        if match:
            if match.group(1) not in by_name:
                problems.append(f"args: {token} names no parameter")
            used.append(match.group(1))
        elif "{" in token or "}" in token:
            problems.append(f"args: {token!r}: a parameter must be a whole token like {{name}}")
        elif "\x00" in token or "\n" in token:
            problems.append("args: a token contains a NUL or a line break")
    cwd = raw.get("cwd")
    if cwd is not None:
        match = TOKEN.match(cwd) if isinstance(cwd, str) else None
        if not match or match.group(1) not in by_name or by_name[match.group(1)].type != "path":
            problems.append("cwd must be {name} of a path parameter (or left out: the working folder)")
        else:
            used.append(match.group(1))
            if not by_name[match.group(1)].required:
                problems.append("cwd: the folder parameter must be required")
    for param in params:
        if used.count(param.name) == 0:
            problems.append(f"parameter {param.name} is not used in args or cwd")
        if param.type == "bool" and f"{{{param.name}}}" not in args:
            problems.append(f"bool parameter {param.name} must be a token of args")
        if cls == "read" and param.type == "path" and param.access == "edit":
            problems.append(f"a read program cannot take an edit path ({param.name}); use class edit")
    if cls in ("edit", "delete") and not any(p.type == "path" and p.access == "edit" for p in params):
        problems.append(f"a {cls} program must declare the paths it changes (a path parameter with access: edit)")
    if len(args) > 100:
        problems.append("args: at most 100 tokens")
    env_pass = _texts(raw.get("env_pass"), "env_pass", problems)
    for env in env_pass:
        problem = env_name_problem(env)
        if problem:
            problems.append(f"env_pass: {problem}")
    env_raw = raw.get("env_set") or {}
    env_set: list[tuple[str, str]] = []
    if not isinstance(env_raw, Mapping):
        problems.append("env_set must be a mapping NAME: value")
    else:
        for env, value in env_raw.items():
            problem = env_name_problem(env)
            if problem:
                problems.append(f"env_set: {problem}")
            if not isinstance(value, (str, int, float)) or isinstance(value, bool) or "\x00" in str(value):
                problems.append(f"env_set.{env} must be a text")
            env_set.append((str(env), str(value)))
            if env in env_pass:
                problems.append(f"{env} is both in env_pass and env_set")
    path_prepend = _texts(raw.get("path_prepend"), "path_prepend", problems)
    for folder in path_prepend:
        if not os.path.isabs(folder) or ":" in folder or "\x00" in folder:
            problems.append(f"path_prepend: {folder!r} must be an absolute folder without ':'")
    interpreter = raw.get("interpreter")
    if interpreter is not None and not (isinstance(interpreter, str) and os.path.isabs(interpreter)):
        problems.append("interpreter must be an absolute path (for example /opt/venv/bin/python)")
    timeout = raw.get("timeout_seconds", 300)
    if not _number(timeout, "int") or not 1 <= timeout <= MAX_TIMEOUT:
        problems.append(f"timeout_seconds must be a whole number from 1 to {MAX_TIMEOUT}")
        timeout = 300
    limits = {}
    for key in ("max_memory_bytes", "max_file_bytes"):
        value = raw.get(key)
        if value is not None and (not _number(value, "int") or value < 1):
            problems.append(f"{key} must be a positive whole number of bytes")
            value = None
        limits[key] = value
    approval = raw.get("approval", "auto")
    if approval not in APPROVALS:
        problems.append("approval must be auto or always (delete and remote always ask)")
    if problems:
        raise SpecError(label, problems)
    return ProgramSpec(name=name, program=program.strip(), description=description.strip(), agent=agent, cls=cls,
                       args=args, params=tuple(params), cwd=cwd, env_pass=env_pass, env_set=tuple(env_set),
                       path_prepend=path_prepend, interpreter=interpreter, timeout_seconds=timeout,
                       max_memory_bytes=limits["max_memory_bytes"], max_file_bytes=limits["max_file_bytes"],
                       approval=approval, source=source)


# ------------------------------------------------------------------ programs declared by tracks

_TRACK_PROGRAMS: dict[str, ProgramSpec] = {}


def register_program(entry: Mapping[str, Any] | ProgramSpec, package: str = "track") -> ProgramSpec:
    """A track package declares a program (same checks as the manifest). The user still trusts it.
    A name that is already declared differently is an error (never a silent override)."""
    spec = entry if isinstance(entry, ProgramSpec) else parse_entry(entry, source=f"track:{package}")
    existing = _TRACK_PROGRAMS.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(f"program {spec.name} is already declared by {existing.source}; names must be unique")
    _TRACK_PROGRAMS[spec.name] = spec
    return spec


def unregister_program(name: str) -> None:
    _TRACK_PROGRAMS.pop(name, None)


def track_programs() -> list[ProgramSpec]:
    return [_TRACK_PROGRAMS[name] for name in sorted(_TRACK_PROGRAMS)]
