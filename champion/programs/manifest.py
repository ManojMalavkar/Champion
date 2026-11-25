"""Where programs come from and whether they are trusted (M2.3).

* The user's manifest ``programs.yaml`` lives in ``programs.folder`` (default
  ``~/.champion/programs``). The folder and the manifest must be owned by the user, not
  writable by group or others, without symbolic links and not inside the working
  folder. ``.champion`` is blocked for every agent tool, so no agent can change them.
* A program file must be a regular file without links, owned by the user or root, not
  group/world-writable, not inside the working folder. How it starts: a declared
  ``interpreter``; a binary (ELF) run directly; or a script whose ``#!`` line names an
  interpreter in a ``path_prepend`` folder or ``/usr/bin`` / ``/bin``
  (``#!/usr/bin/env NAME`` is looked up in that order, the PATH the program gets).
* Interpreters (user decision 3, 25 Nov 2026): the *requested* path (as declared, on
  the ``#!`` line or found on that PATH) may be a link, for example a venv ``python``;
  the *resolved* file (links followed) must pass the program-file rules and is hashed,
  system interpreters included. Both paths and the hash are pinned; an OS update that
  changes an interpreter needs a new trust.
* Trust: ``trust(config, name)`` pins the program path and SHA-256, the interpreter
  (requested, resolved, SHA-256) and the entry digest in ``programs.trust_file`` (mode
  600). A program is offered only while all of them match; ``state_of`` runs again just
  before every start.
* Names: the same name twice in the manifest, or in the manifest and a track, refuses
  every entry with that name (never a silent override). The manifest is read with
  duplicate mapping keys refused, so a second ``class:`` cannot hide the first.

Status of a program: ``trusted``, ``untrusted`` (never trusted), ``changed`` (file,
interpreter or entry differ from the pinned values), ``missing`` (file not found),
``invalid`` (entry or file breaks a rule).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from champion.execution.commands import SYSTEM_BIN
from champion.policy.access import is_within
from champion.policy.scope import find_symlink
from champion.programs.spec import ProgramSpec, SpecError, parse_entry, track_programs

MANIFEST = "programs.yaml"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PROGRAM_BYTES = 4 * 1024 ** 3
TRUSTED, UNTRUSTED, CHANGED, MISSING, INVALID = "trusted", "untrusted", "changed", "missing", "invalid"


class ProgramError(Exception):
    """A program cannot be used or trusted; the message says why."""


@dataclass(frozen=True)
class ProgramState:
    name: str
    status: str
    detail: str = ""
    spec: ProgramSpec | None = None
    path: str | None = None  # resolved program file
    argv0: tuple[str, ...] = ()  # interpreter (and its option) + program, or the binary
    file_sha256: str = ""
    interpreter_requested: str = ""  # as declared / on the #! line / found on PATH
    interpreter_resolved: str = ""  # the real file that runs (links followed); hashed
    interpreter_sha256: str = ""
    source: str = "manifest"

    @property
    def executable(self) -> str:
        return self.argv0[0]


# ------------------------------------------------------------------ file rules

def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_problem(path: str, working_directory: Path | None, *, executable: bool, what: str = "program") -> str | None:
    """Why ``path`` cannot be trusted, or None."""
    if not os.path.isabs(path):
        return f"{what} {path} is not an absolute path"
    link = find_symlink(Path(path))
    if link is not None:
        return f"{what} {path}: {link} is a symbolic link; give the real path"
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return f"{what} {path} does not exist"
    if not stat.S_ISREG(info.st_mode):
        return f"{what} {path} is not a regular file"
    if info.st_uid not in (os.getuid(), 0):
        return f"{what} {path} belongs to another user (uid {info.st_uid})"
    if info.st_mode & 0o022:
        return f"{what} {path} can be changed by group or others (mode {stat.filemode(info.st_mode)})"
    if info.st_size > MAX_PROGRAM_BYTES:
        return f"{what} {path} is larger than 4 GiB"
    if executable and not os.access(path, os.X_OK):
        return f"{what} {path} is not executable"
    if working_directory is not None and is_within(Path(path), working_directory):
        return f"{what} {path} is inside the working folder {working_directory}, where agents can edit files"
    return None


def folder_problem(folder: str, working_directory: Path | None) -> str | None:
    link = find_symlink(Path(folder))
    if link is not None:
        return f"programs folder {folder}: {link} is a symbolic link"
    info = os.lstat(folder)
    if not stat.S_ISDIR(info.st_mode):
        return f"programs folder {folder} is not a folder"
    if info.st_uid != os.getuid():
        return f"programs folder {folder} belongs to another user"
    if info.st_mode & 0o022:
        return f"programs folder {folder} can be changed by group or others (mode {stat.filemode(info.st_mode)})"
    if working_directory is not None and is_within(Path(folder), working_directory):
        return f"programs folder {folder} is inside the working folder {working_directory}"
    return None


@dataclass(frozen=True)
class Start:
    argv0: tuple[str, ...]
    requested: str = ""
    resolved: str = ""


def resolve_interpreter(requested: str, working_directory: Path | None, where: str) -> str:
    """The real file behind ``requested`` (links followed), checked like a program file."""
    if not os.path.isabs(requested):
        raise ProgramError(f"{where}: interpreter {requested} is not an absolute path")
    if not os.path.lexists(requested):
        raise ProgramError(f"{where}: interpreter {requested} does not exist")
    resolved = os.path.realpath(requested)
    problem = file_problem(resolved, working_directory, executable=True, what="interpreter")
    if problem:
        raise ProgramError(f"{where}: {problem}" + (f" ({requested} points to it)" if resolved != requested else ""))
    return resolved


def resolve_start(path: str, spec: ProgramSpec, working_directory: Path | None) -> Start:
    """How the program starts: (interpreter, [option], program) or (binary,)."""
    search = (*spec.path_prepend, *SYSTEM_BIN)
    if spec.interpreter:
        resolved = resolve_interpreter(spec.interpreter, working_directory, path)
        return Start((spec.interpreter, path), spec.interpreter, resolved)
    with open(path, "rb") as stream:
        head = stream.read(256)
    if head.startswith(b"\x7fELF"):
        if not os.access(path, os.X_OK):
            raise ProgramError(f"binary {path} is not executable")
        return Start((path,))
    if not head.startswith(b"#!"):
        raise ProgramError(f"{path} has no #! line and is not a binary; give interpreter: in its entry")
    line = head[2:].split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
    try:
        words = shlex.split(line)
    except ValueError:
        words = []
    if not words:
        raise ProgramError(f"{path}: empty #! line")
    if words[0] == "/usr/bin/env":
        words = words[1:]
        if not words or "/" in words[0] or words[0].startswith("-"):
            raise ProgramError(f"{path}: #!/usr/bin/env needs a program name")
        found = next((os.path.join(d, words[0]) for d in search if os.path.isfile(os.path.join(d, words[0]))),
                     None)
        if found is None:
            raise ProgramError(f"{path}: {words[0]} is not in {', '.join(search)}; give interpreter: "
                               "in its entry")
        words[0] = found
    if os.path.dirname(words[0]) not in search:
        raise ProgramError(f"{path}: interpreter {words[0]} is not in {', '.join(search)}; give "
                           "interpreter: in its entry to use another one")
    if len(words) > 2 or (len(words) == 2 and not words[1].startswith("-")):
        raise ProgramError(f"{path}: #! line may give the interpreter and at most one option")
    resolved = resolve_interpreter(words[0], working_directory, path)
    return Start((*words, path), words[0], resolved)


# ------------------------------------------------------------------ manifest

class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses a key given twice in one mapping."""


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)

def load_manifest(config: Any, working_directory: Path | None) -> tuple[list[ProgramSpec], list[ProgramState]]:
    """Entries of the user's manifest and the entries/problems that cannot be used."""
    folder = config.get("programs.folder")
    if not folder or not os.path.lexists(folder):
        return [], []
    problem = folder_problem(folder, working_directory)
    if problem:
        return [], [ProgramState(MANIFEST, INVALID, problem)]
    file = os.path.join(folder, MANIFEST)
    if not os.path.lexists(file):
        return [], []
    problem = file_problem(file, working_directory, executable=False, what="manifest")
    if problem is None and os.path.getsize(file) > MAX_MANIFEST_BYTES:
        problem = f"manifest {file} is larger than 1 MiB"
    if problem:
        return [], [ProgramState(MANIFEST, INVALID, problem)]
    try:
        with open(file, encoding="utf-8") as stream:
            data = yaml.load(stream, Loader=_UniqueKeyLoader) or {}  # noqa: S506 - a SafeLoader subclass
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        why = getattr(exc, "problem", None) or type(exc).__name__
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        return [], [ProgramState(MANIFEST, INVALID, f"{file} cannot be read: {why}{where}")]
    entries = data.get("programs") if isinstance(data, dict) else None
    if not isinstance(entries, list) or set(data) - {"programs"}:
        return [], [ProgramState(MANIFEST, INVALID, f"{file} must contain only 'programs:' with a list of entries")]
    specs, bad = [], []
    for number, entry in enumerate(entries, 1):
        try:
            specs.append(parse_entry(entry))
        except SpecError as exc:
            name = exc.name if exc.name != "?" else f"entry {number}"
            bad.append(ProgramState(name, INVALID, "; ".join(exc.problems)))
    return specs, bad


def all_specs(config: Any, working_directory: Path | None) -> tuple[list[ProgramSpec], list[ProgramState]]:
    specs, bad = load_manifest(config, working_directory)
    seen = {s.name for s in specs}
    for spec in track_programs():
        if spec.name in seen:
            bad.append(ProgramState(spec.name, INVALID, f"name used by the manifest and by {spec.source}; "
                                    "both are refused until one is renamed", spec=spec, source=spec.source))
            specs = [s for s in specs if s.name != spec.name]
            continue
        seen.add(spec.name)
        specs.append(spec)
    names = [s.name for s in specs]
    for name in sorted({n for n in names if names.count(n) > 1}):
        bad.append(ProgramState(name, INVALID, "the manifest has two entries with this name; both are refused"))
        specs = [s for s in specs if s.name != name]
    return specs, bad


def resolve_program(spec: ProgramSpec, config: Any) -> str:
    if spec.program.startswith("config:"):
        key = spec.program[len("config:"):]
        value = config.get(key)
        if not isinstance(value, str) or not value:
            raise ProgramError(f"config key {key} (the path of {spec.name}) is not set")
        return os.path.normpath(value)
    if os.path.isabs(spec.program):
        return os.path.normpath(spec.program)
    folder = config.get("programs.folder")
    if not folder or spec.source != "manifest":
        raise ProgramError(f"program path {spec.program} must be absolute")
    path = os.path.normpath(os.path.join(folder, spec.program))
    if not is_within(Path(path), Path(folder)):
        raise ProgramError(f"program path {spec.program} leaves the programs folder")
    return path


# ------------------------------------------------------------------ trust store

def _trust_path(config: Any) -> str:
    return str(config.get("programs.trust_file"))


def read_trust(config: Any) -> dict[str, dict]:
    file = _trust_path(config)
    if not os.path.lexists(file):
        return {}
    problem = file_problem(file, None, executable=False, what="trust file")
    if problem is None and os.lstat(file).st_uid != os.getuid():
        problem = f"trust file {file} belongs to another user"
    if problem:
        raise ProgramError(problem)
    try:
        with open(file, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError) as exc:
        raise ProgramError(f"trust file {file} cannot be read ({type(exc).__name__})") from None
    if not isinstance(data, dict) or not isinstance(data.get("programs"), dict):
        raise ProgramError(f"trust file {file} has an unknown format")
    return data["programs"]


def _write_trust(config: Any, entries: dict[str, dict]) -> None:
    file = _trust_path(config)
    folder = os.path.dirname(file)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".programs-trust-", dir=folder)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"format": 1, "programs": entries}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp, file)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


# ------------------------------------------------------------------ status

def state_of(spec: ProgramSpec, config: Any, working_directory: Path | None,
             trust: dict[str, dict] | None = None) -> ProgramState:
    """Status of one program now (file and interpreter hashed again)."""
    base = dict(name=spec.name, spec=spec, source=spec.source)
    try:
        path = resolve_program(spec, config)
    except ProgramError as exc:
        return ProgramState(status=INVALID, detail=str(exc), **base)
    if not os.path.lexists(path):
        return ProgramState(status=MISSING, detail=f"{path} does not exist", path=path, **base)
    problem = file_problem(path, working_directory, executable=False)  # binaries: checked in start_argv
    if problem:
        return ProgramState(status=INVALID, detail=problem, path=path, **base)
    maximum = int(config.get("programs.max_timeout_seconds", 3600))
    if spec.timeout_seconds > maximum:
        return ProgramState(status=INVALID, path=path, detail=f"timeout_seconds {spec.timeout_seconds} is above "
                            f"programs.max_timeout_seconds ({maximum})", **base)
    try:
        start = resolve_start(path, spec, working_directory)
        file_sha = sha256_file(path)
        interpreter_sha = sha256_file(start.resolved) if start.resolved else ""  # every interpreter (decision 3)
    except ProgramError as exc:
        return ProgramState(status=INVALID, detail=str(exc), path=path, **base)
    except OSError as exc:
        return ProgramState(status=INVALID, detail=f"{path} cannot be read ({exc.strerror})", path=path, **base)
    found = dict(path=path, argv0=start.argv0, file_sha256=file_sha, interpreter_requested=start.requested,
                 interpreter_resolved=start.resolved, interpreter_sha256=interpreter_sha, **base)
    if trust is None:
        try:
            trust = read_trust(config)
        except ProgramError as exc:
            return ProgramState(status=INVALID, detail=str(exc), **found)
    pinned = trust.get(spec.name)
    if not pinned:
        return ProgramState(status=UNTRUSTED, detail=f"not trusted yet: champion programs trust {spec.name}", **found)
    changed = [label for label, key, value in (("program file", "file_sha256", file_sha),
                                               ("interpreter path", "interpreter_requested", start.requested),
                                               ("interpreter target", "interpreter_resolved", start.resolved),
                                               ("interpreter", "interpreter_sha256", interpreter_sha),
                                               ("entry", "entry_sha256", spec.fingerprint),
                                               ("path", "path", path))
               if pinned.get(key) != value]
    if changed:
        return ProgramState(status=CHANGED, detail=f"{', '.join(changed)} changed since it was trusted; check it, "
                            f"then champion programs trust {spec.name}", **found)
    return ProgramState(status=TRUSTED, detail=f"trusted {pinned.get('trusted_at', '')[:19]}", **found)


def program_states(config: Any, working_directory: Path | None) -> list[ProgramState]:
    """Every program the user or a track declared, with its status; problems of the manifest first."""
    specs, bad = all_specs(config, working_directory)
    try:
        trust = read_trust(config)
    except ProgramError as exc:
        return bad + [ProgramState("trust file", INVALID, str(exc))] + \
            [ProgramState(s.name, UNTRUSTED, "trust file cannot be read", spec=s, source=s.source) for s in specs]
    return bad + [state_of(spec, config, working_directory, trust) for spec in specs]


def trust(config: Any, name: str, working_directory: Path | None) -> ProgramState:
    """Pin the current hashes of program ``name`` (user command only)."""
    specs, bad = all_specs(config, working_directory)
    for state in bad:
        if state.name == name:
            raise ProgramError(f"{name} cannot be trusted: {state.detail}")
    spec = next((s for s in specs if s.name == name), None)
    if spec is None:
        raise ProgramError(f"there is no program named {name}")
    entries = read_trust(config)
    state = state_of(spec, config, working_directory, entries)
    if state.status in (INVALID, MISSING):
        raise ProgramError(f"{name} cannot be trusted: {state.detail}")
    entries[name] = {"path": state.path, "file_sha256": state.file_sha256,
                     "interpreter_requested": state.interpreter_requested,
                     "interpreter_resolved": state.interpreter_resolved,
                     "interpreter_sha256": state.interpreter_sha256, "entry_sha256": spec.fingerprint,
                     "source": spec.source, "trusted_at": datetime.now(timezone.utc).isoformat()}
    _write_trust(config, entries)
    return state_of(spec, config, working_directory, entries)


def untrust(config: Any, name: str) -> bool:
    entries = read_trust(config)
    if name not in entries:
        return False
    del entries[name]
    _write_trust(config, entries)
    return True
