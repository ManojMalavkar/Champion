"""Champion configuration.

How the final configuration is built:

1. Start from the built-in ``DEFAULTS`` below (so every key always exists).
2. Merge the YAML config file on top. The file is found in this order:
   explicit ``path`` argument > ``CHAMPION_CONFIG`` environment variable >
   ``<project>/config/config.yaml``. Without a file, defaults are used.
3. Normalise values (access level as text), resolve relative paths, validate.

Relative paths:
* ``execution.working_directory`` is relative to the directory Champion is
  started from (the folder you work in).
* All other paths (storage, kb, agent sections) are relative to the project root: the
  parent of the ``config/`` folder that holds the file, or the file's own
  folder if it is not inside a ``config/`` folder.

Sections of agents (step M0): a specialist package can declare its own
top-level section (``SpecialistSpec.config_section``). ``load_config`` first
loads the specialists, then adds each registered section's defaults, merges
the file, resolves its path keys and runs its validator, exactly like a
built-in section. The example values of such a section live in
``config/examples/<section>.yaml`` (checked by ``example_fragment_problems``).

Step M1: the file limits used by the core tools moved from ``pydyna`` to ``files``
(``large_file_bytes``, ``max_model_bytes``, ``model_timeout_seconds``); the old
``pydyna.<key>`` names still work with a warning. The rest of the ``pydyna`` section
belongs to the PyDyna package (its own ``config_section``), like every agent's
``<section>.knowledge_root``.

The API key is never stored in the config. ``get_openai_api_key`` reads the
configured environment variable from the environment or a ``.env`` file in the
project root, without putting it into ``os.environ``.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

MiB = 1024 * 1024
GiB = 1024 * MiB

ACCESS_LEVELS = ("1.1", "1.2", "2.1", "2.2", "custom")
# Custom level scopes, from narrowest to widest. "outside" means the working
# directory and everything outside it (still subject to the blocked list).
CUSTOM_SCOPES = ("none", "working", "outside")
# Rights per folder (step 4e); "write" is accepted as the old name of "edit".
ACCESS_RIGHTS = ("none", "read", "edit", "write")
PLACEHOLDER_MODEL = "REPLACE_WITH_MODEL_ID"

# openai: OpenAI API (Responses API). databricks: Unity AI Gateway of a
# Databricks workspace (OpenAI-compatible Chat Completions API).
PROVIDERS = ("openai", "databricks")

DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "openai",
        "model_id": PLACEHOLDER_MODEL,
        "model_ids": [],  # optional fallback models, tried in order
        "api_key_env": "OPENAI_API_KEY",
        "base_url": None,  # None = api.openai.com; set later for a gateway
        "tracing": False,  # SDK traces would be sent to OpenAI
        "verify_tls": True,  # false = like curl -k; only allowed with an internal llm.base_url
    },
    "orchestration": {
        "max_steps": 8,  # orchestrator max_turns
        "specialist_max_turns": 20,  # nested runs (PyDyna: 20 tool rounds)
        "history_turns": 10,  # earlier conversation turns sent with each request
        "prevent_repeated_action": True,
    },
    "policy": {
        "default_level": "1.1",
        "custom": {"read": "working", "write": "none", "extra_dirs": []},
    },
    "access": {  # step 4e: per-folder access on top of a preset
        "preset": None,  # 1.1, 1.2, 2.1, 2.2 or custom; None = policy.default_level
        "working": None,  # none | read | edit; None = from the preset
        "outside": None,  # everything else: none | read | edit; None = from the preset
        "folders": [],  # [{path: /data/models, right: read}, ...]
        "file": "~/.champion/access.yaml",  # where /access save writes; null = saving off
    },
    "execution": {
        "working_directory": ".",
        "require_approval": True,
        "timeout_seconds": 120,
        "max_output_chars": 12000,
        "max_memory_bytes": 4 * GiB,
        "max_cpu_seconds": 300,
        "max_output_file_bytes": 100 * MiB,
        "max_command_file_bytes": 8 * GiB,  # largest file a shell command may write (step 4a)
    },
    "edit": {
        "full_backup": False,
        # step 4d: a higher-risk edit asks the user once (Approve? [y/N])
        "approval_max_deleted_lines": 100,    # more lines deleted than this
        "approval_max_changed_fraction": 0.2,  # more of the file changed than this ...
        "approval_min_bytes": 8192,           # ... for files of at least this size
        "approval_max_files": 10,             # a change set with more files than this
        "max_write_bytes": 1 * MiB,           # file_create / file_write content (at most 10 MiB)
    },
    # Checks the Coder Agent may run with run_check (the user approves every run), e.g.
    # tests: ["/path/to/venv/bin/python", "-m", "pytest", "-q"]
    "coder": {"checks": {}, "check_env": ["LD_LIBRARY_PATH"]},
    "storage": {"audit_db": "champion.db", "log_dir": "logs"},
    "agents": {"linux_coder": {"enabled": True}, "coder": {"enabled": True}},
    "files": {  # M1: limits of the core file tools (were pydyna.*)
        "large_file_bytes": 100 * MiB,  # files this size or larger: no whole read, grep skips them
        "max_model_bytes": 8 * GiB,  # larger files are refused (edit, hash)
        "model_timeout_seconds": 120,  # time limit of one pass over a large file
    },
    "knowledge": {  # M1: knowledge layer (champion/knowledge/), roots in <section>.knowledge_root
        "offline": True,  # embedding model from the local cache only (HF_HUB_OFFLINE=1)
        "verify_hash": True,  # SHA-256 of the database files before and after opening
        "max_results": 20,  # most results one search or lookup returns
        "fetch_factor": 5,  # with a track filter: fetch this many times more, then filter
        "max_hit_chars": 1500,  # text of one result is cut to this
    },
    "kb": {
        "sources": {},
        "max_source_file_bytes": 2 * MiB,
    },
    "mcp": {"enabled": False, "servers": {}},
    "memory": {  # M6 (CR-2): memory layers L0-L3 (champion/memory/); off until checked on Linux
        "enabled": False,
        "max_turns": 8,            # earlier turns sent with a request (replaces orchestration.history_turns)
        "max_tokens": 16000,       # estimated budget for earlier turns + the new input
        "tool_output_chars": 2000,  # tool outputs of older turns are cut to this
        "full_tool_turns": 2,      # the last N turns keep their tool outputs whole
        "runtime_state": True,     # L0: <working folder>/.champion/runtime/
        "keep_archived": 50,       # ended requests kept in runtime/archive/
        "summaries": True,         # L2: dropped turns are summarised after the turn
        "preferences": True,       # L3: /memory set ...
        "user_root": "~/.champion/memory",  # ~ = the real home folder
        "project": None,           # folder whose .champion/memory keeps the summaries; None = user_root
    },
}

# Sections whose sub-keys are free (new agents, MCP servers, kb projects).
OPEN_SECTIONS = {"agents", "mcp.servers", "kb.sources", "coder.checks"}
# coder.check_env: variables copied from Champion's environment into run_check only
# (for example LD_LIBRARY_PATH for a Python built with shared libraries). These stay fixed:
CHECK_ENV_FIXED = {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "LD_PRELOAD", "LD_AUDIT", "PYTHONSTARTUP",
                   "BASH_ENV", "ENV"}
SECRET_NAME = r"TOKEN|SECRET|PASSW|KEY|CREDENTIAL|AUTH|COOKIE|SESSION"

# Paths resolved against the project root (working_directory is special).
ROOT_PATH_KEYS = (
    "storage.audit_db",
    "storage.log_dir",
    "access.file",
)

# M1: these keys moved from pydyna to files; old names are taken over with a warning.
MOVED_FILE_KEYS = ("large_file_bytes", "max_model_bytes", "model_timeout_seconds")


class ConfigError(ValueError):
    """Raised when the config file is missing, unreadable or invalid."""


# --------------------------------------------------- sections of agents (M0)

SECTION_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


@dataclass(frozen=True)
class ConfigSection:
    name: str
    defaults: Mapping[str, Any] = field(hash=False)
    validate: Callable[[Mapping[str, Any]], list[str]] | None = field(default=None, compare=False)
    path_keys: tuple[str, ...] = ()
    owner: str = ""  # specialist that declared it


_SECTIONS: dict[str, ConfigSection] = {}


def register_config_section(name: str, defaults: Mapping[str, Any], *,
                            validate: Callable[[Mapping[str, Any]], list[str]] | None = None,
                            path_keys: tuple[str, ...] = (), owner: str = "") -> ConfigSection:
    """Add a top-level config section owned by an agent. Built-in sections cannot be replaced."""
    if not SECTION_NAME.match(name or ""):
        raise ValueError(f"config section {name!r}: use 2-31 lowercase letters, digits or _")
    if name in DEFAULTS:
        raise ValueError(f"config section {name} is a built-in section of Champion")
    if not isinstance(defaults, Mapping):
        raise ValueError(f"config section {name}: defaults must be a mapping")
    unknown = [key for key in path_keys if key not in defaults]
    if unknown:
        raise ValueError(f"config section {name}: path keys not in the defaults: {', '.join(unknown)}")
    section = ConfigSection(name, copy.deepcopy(dict(defaults)), validate, tuple(path_keys), owner)
    existing = _SECTIONS.get(name)
    if existing is not None and (existing.owner != owner or existing.defaults != section.defaults):
        raise ValueError(f"config section {name} is already declared by {existing.owner or 'another agent'}")
    _SECTIONS[name] = section
    return section


def unregister_config_section(name: str) -> None:
    _SECTIONS.pop(name, None)


def config_sections() -> list[ConfigSection]:
    return [_SECTIONS[name] for name in sorted(_SECTIONS)]


def _load_agent_sections() -> None:
    """Import the specialist packages so their sections are registered (import errors are
    reported by the registry when the specialists are selected, not here)."""
    from champion.specialists.registry import load_builtin_specialists

    load_builtin_specialists()


def example_fragment_problems(folder: Path | None = None) -> list[str]:
    """Every agent section has ``config/examples/<section>.yaml`` with exactly its defaults."""
    folder = folder or project_root() / "config" / "examples"
    problems = []
    for section in config_sections():
        file = folder / f"{section.name}.yaml"
        if not file.is_file():
            problems.append(f"{section.name}: example file {file.name} is missing")
            continue
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if raw != {section.name: section.defaults}:
            problems.append(f"{section.name}: {file.name} must contain exactly the section's defaults")
    known = {section.name for section in config_sections()}
    for file in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        if file.stem not in known:
            problems.append(f"{file.name}: no agent declares a config section {file.stem}")
    return problems


@dataclass
class Config:
    data: dict[str, Any]
    path: Path | None  # config file used, None when only defaults are used
    root: Path  # base for relative paths
    warnings: list[str] = field(default_factory=list)

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a value with a dotted key, for example ``config.get("llm.model_id")``."""
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node


def project_root() -> Path:
    """Folder that contains ``champion/`` and ``config/``."""
    return Path(__file__).resolve().parents[2]


def find_config_file(path: str | os.PathLike | None = None,
                     env: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    if path is not None:
        target = Path(path).expanduser()
        if not target.is_file():
            raise ConfigError(f"Config file not found: {target}")
        return target.resolve()
    if env.get("CHAMPION_CONFIG"):
        target = Path(env["CHAMPION_CONFIG"]).expanduser()
        if not target.is_file():
            raise ConfigError(f"CHAMPION_CONFIG points to a missing file: {target}")
        return target.resolve()
    default = project_root() / "config" / "config.yaml"
    return default.resolve() if default.is_file() else None


def load_config(path: str | os.PathLike | None = None, *,
                env: Mapping[str, str] | None = None,
                cwd: str | os.PathLike | None = None) -> Config:
    """Build the configuration (defaults + file), resolve paths and validate it."""
    try:
        working_directory = Path(cwd) if cwd is not None else Path.cwd()
    except FileNotFoundError as exc:
        raise ConfigError("Current directory no longer exists; cd to an existing directory and try again") from exc
    file = find_config_file(path, env)
    _load_agent_sections()
    sections = config_sections()
    data = copy.deepcopy(DEFAULTS)
    for section in sections:
        data[section.name] = copy.deepcopy(dict(section.defaults))
    warnings: list[str] = []
    if file is not None:
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {file}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{file} must contain a mapping at the top level")
        _move_file_keys(raw, warnings)
        _merge(data, raw, "", warnings)

    root = project_root() if file is None else _base_dir(file)
    _normalise(data)
    _resolve_paths(data, root, working_directory)
    for section in sections:
        for key in section.path_keys:
            data[section.name][key] = _resolve(data[section.name][key], root)
    errors = validate(data) + _validate_sections(data, sections)
    if errors:
        where = file or "defaults"
        raise ConfigError(f"Invalid configuration ({where}):\n- " + "\n- ".join(errors))
    return Config(data=data, path=file, root=root, warnings=warnings)


def _validate_sections(data: dict, sections: list[ConfigSection]) -> list[str]:
    errors = []
    for section in sections:
        if section.validate is None:
            continue
        try:
            problems = list(section.validate(data[section.name]) or [])
        except Exception as exc:  # a broken validator must not hide the config
            problems = [f"validator failed ({type(exc).__name__})"]
        errors += [f"{section.name}: {problem}" for problem in problems]
    return errors


def _move_file_keys(raw: dict, warnings: list[str]) -> None:
    """M1: ``pydyna.large_file_bytes`` and the like are now ``files.*`` (old names still work)."""
    old = raw.get("pydyna")
    if not isinstance(old, dict):
        return
    new = raw.get("files")
    if new is None:
        new = raw["files"] = {}
    if not isinstance(new, dict):
        return  # reported by _merge
    for key in MOVED_FILE_KEYS:
        if key in old:
            value = old.pop(key)
            if key in new:
                warnings.append(f"'pydyna.{key}' is ignored: 'files.{key}' is set (remove the old key)")
            else:
                new[key] = value
                warnings.append(f"'pydyna.{key}' is now 'files.{key}' (still used; please rename it)")
    if not old:
        raw.pop("pydyna")


def _base_dir(file: Path) -> Path:
    return file.parent.parent if file.parent.name == "config" else file.parent


def _merge(base: dict, override: Mapping, prefix: str, warnings: list[str]) -> None:
    """Merge ``override`` into ``base``; unknown keys are reported and ignored."""
    for key, value in override.items():
        dotted = f"{prefix}{key}"
        if prefix.rstrip(".") in OPEN_SECTIONS:
            base[key] = copy.deepcopy(value)
            continue
        if key not in base:
            warnings.append(f"Unknown config key '{dotted}' (ignored; check spelling)")
            continue
        if isinstance(base[key], dict):
            if not isinstance(value, Mapping):
                raise ConfigError(f"'{dotted}' must be a mapping")
            _merge(base[key], value, dotted + ".", warnings)
        else:
            base[key] = value


def _normalise(data: dict) -> None:
    # YAML reads an unquoted 1.1 as a float; accept it and store text.
    level = data["policy"]["default_level"]
    if isinstance(level, (int, float)) and not isinstance(level, bool):
        level = f"{level:.1f}"
    if isinstance(level, str):
        data["policy"]["default_level"] = level.strip().lower()
    custom = data["policy"]["custom"]
    for key in ("read", "write"):
        if isinstance(custom.get(key), str):
            custom[key] = custom[key].strip().lower()
    access = data["access"]
    preset = access.get("preset")
    if isinstance(preset, (int, float)) and not isinstance(preset, bool):
        preset = f"{preset:.1f}"
    if isinstance(preset, str):
        access["preset"] = preset.strip().lower()
    for key in ("working", "outside"):
        if isinstance(access.get(key), str):
            access[key] = access[key].strip().lower()


def _resolve(value: Any, base: Path) -> Any:
    if value is None or not isinstance(value, (str, os.PathLike)) or str(value) == "":
        return value
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (base / path).resolve())


def _resolve_paths(data: dict, root: Path, cwd: Path) -> None:
    data["execution"]["working_directory"] = _resolve(data["execution"]["working_directory"], cwd)
    for dotted in ROOT_PATH_KEYS:
        section, key = dotted.split(".")
        data[section][key] = _resolve(data[section][key], root)
    sources = data["kb"]["sources"]
    for name in list(sources):
        sources[name] = _resolve(sources[name], root)
    extra = data["policy"]["custom"].get("extra_dirs")
    if isinstance(extra, list):
        data["policy"]["custom"]["extra_dirs"] = [_resolve(d, cwd) for d in extra]
    memory = data["memory"]
    for key, base in (("user_root", root), ("project", cwd)):
        if isinstance(memory.get(key), str) and memory[key]:
            from champion.policy.scope import expand_home  # ~ = real home (links resolved)

            memory[key] = _resolve(expand_home(memory[key]), base)


# --------------------------------------------------------------- validation

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate(data: dict) -> list[str]:
    """Return a list of human-readable problems (empty when valid)."""
    errors: list[str] = []

    def get(dotted: str) -> Any:
        node: Any = data
        for part in dotted.split("."):
            node = node[part]
        return node

    def positive_int(dotted: str, maximum: int | None = None) -> None:
        value = get(dotted)
        if not _is_int(value) or value <= 0 or (maximum is not None and value > maximum):
            limit = f" and at most {maximum}" if maximum is not None else ""
            errors.append(f"{dotted} must be a whole number above 0{limit} (got {value!r})")

    def boolean(dotted: str) -> None:
        if not isinstance(get(dotted), bool):
            errors.append(f"{dotted} must be true or false (got {get(dotted)!r})")

    llm = data["llm"]
    if isinstance(llm["provider"], str):
        llm["provider"] = llm["provider"].strip().lower()
    if llm["provider"] not in PROVIDERS:
        errors.append(f"llm.provider must be one of {', '.join(PROVIDERS)} (got {llm['provider']!r})")
    if not isinstance(llm["model_id"], str) or not llm["model_id"].strip():
        errors.append("llm.model_id must be a non-empty text")
    if not isinstance(llm["model_ids"], list) or not all(isinstance(m, str) and m for m in llm["model_ids"]):
        errors.append("llm.model_ids must be a list of model names")
    api_key_env = llm["api_key_env"]
    if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
        errors.append("llm.api_key_env must be a valid environment variable name")
    base_url = llm["base_url"]
    if base_url is not None and not (isinstance(base_url, str) and base_url.startswith(("https://", "http://"))):
        errors.append("llm.base_url must be empty or an http(s) URL")
    if llm["provider"] == "databricks":
        # Databricks models are reached only through the Unity AI Gateway
        # (Chat Completions API); legacy /serving-endpoints are not allowed.
        if not (isinstance(base_url, str) and "/ai-gateway/" in base_url):
            errors.append("llm.base_url must be the workspace AI Gateway URL "
                          "(https://<workspace>/ai-gateway/mlflow/v1) when llm.provider is databricks")
        if llm["tracing"] is True:
            errors.append("llm.tracing must be false when llm.provider is databricks "
                          "(SDK traces would be sent to OpenAI)")
    boolean("llm.tracing")
    boolean("llm.verify_tls")
    if llm["verify_tls"] is False:
        # Certificate checking off is only for an internal server given in llm.base_url, never for
        # api.openai.com or a Databricks workspace (both have public certificates).
        if not isinstance(base_url, str):
            errors.append("llm.verify_tls: false needs llm.base_url (an internal server); "
                          "certificate checking stays on for api.openai.com")
        elif llm["provider"] == "databricks" or "openai.com" in base_url.lower():
            errors.append("llm.verify_tls must be true for api.openai.com and the Databricks AI Gateway")

    positive_int("orchestration.max_steps", 50)
    positive_int("orchestration.specialist_max_turns", 100)
    positive_int("orchestration.history_turns", 100)
    boolean("orchestration.prevent_repeated_action")

    level = data["policy"]["default_level"]
    if level not in ACCESS_LEVELS:
        errors.append(f"policy.default_level must be one of {', '.join(ACCESS_LEVELS)} (got {level!r})")
    access = data["access"]
    if access.get("preset") is not None and access["preset"] not in ACCESS_LEVELS:
        errors.append(f"access.preset must be one of {', '.join(ACCESS_LEVELS)} or null (got {access['preset']!r})")
    for key in ("working", "outside"):
        if access.get(key) is not None and access[key] not in ACCESS_RIGHTS:
            errors.append(f"access.{key} must be one of {', '.join(ACCESS_RIGHTS)} or null (got {access[key]!r})")
    folders = access.get("folders")
    if not isinstance(folders, list):
        errors.append("access.folders must be a list of {path: FOLDER, right: none|read|edit}")
    else:
        for i, entry in enumerate(folders, 1):
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]
                    or str(entry.get("right", "read")).strip().lower() not in ACCESS_RIGHTS):
                errors.append(f"access.folders entry {i} must be {{path: FOLDER, right: none|read|edit}} "
                              f"(got {entry!r})")
    if access.get("file") is not None and not isinstance(access["file"], str):
        errors.append("access.file must be a file path or null")
    custom = data["policy"]["custom"]
    read, write = custom.get("read"), custom.get("write")
    for key, value in (("read", read), ("write", write)):
        if value not in CUSTOM_SCOPES:
            errors.append(f"policy.custom.{key} must be one of {', '.join(CUSTOM_SCOPES)} (got {value!r})")
    if read in CUSTOM_SCOPES and write in CUSTOM_SCOPES and CUSTOM_SCOPES.index(write) > CUSTOM_SCOPES.index(read):
        errors.append("policy.custom.write cannot be wider than policy.custom.read")
    extra = custom.get("extra_dirs")
    if not isinstance(extra, list) or not all(isinstance(d, str) and d for d in extra):
        errors.append("policy.custom.extra_dirs must be a list of folder paths")

    if not isinstance(data["execution"]["working_directory"], str):
        errors.append("execution.working_directory must be a folder path")
    boolean("execution.require_approval")
    for key in ("timeout_seconds", "max_output_chars", "max_memory_bytes", "max_cpu_seconds", "max_output_file_bytes",
                "max_command_file_bytes"):
        positive_int(f"execution.{key}")
    boolean("edit.full_backup")
    positive_int("edit.approval_max_deleted_lines")
    positive_int("edit.approval_min_bytes")
    positive_int("edit.approval_max_files")
    positive_int("edit.max_write_bytes", 10 * MiB)
    checks = get("coder.checks")
    if not isinstance(checks, dict):
        errors.append("coder.checks must be a mapping of name -> command (a list of words)")
    else:
        for name, argv in checks.items():
            if not (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,30}", name)):
                errors.append(f"coder.checks: {name!r} is not a valid check name (letters, digits, - and _)")
            elif (not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv)
                  or ("/" in argv[0] and not argv[0].startswith("/"))):
                errors.append(f"coder.checks.{name} must be a list of words, the first a program in /usr/bin or "
                              f"an absolute path (got {argv!r})")
    check_env = get("coder.check_env")
    if not isinstance(check_env, list):
        errors.append(f"coder.check_env must be a list of environment variable names (got {check_env!r})")
    else:
        for name in check_env:
            if not (isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)):
                errors.append(f"coder.check_env: {name!r} is not an environment variable name")
            elif name.upper() in CHECK_ENV_FIXED or re.search(SECRET_NAME, name, re.IGNORECASE):
                errors.append(f"coder.check_env: {name} cannot be passed to checks (fixed by Champion or "
                              "possibly a secret)")
    fraction = get("edit.approval_max_changed_fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 < fraction <= 1:
        errors.append(f"edit.approval_max_changed_fraction must be a number above 0 and at most 1 (got {fraction!r})")

    for key in MOVED_FILE_KEYS:
        positive_int(f"files.{key}")
    files = data["files"]
    if _is_int(files["large_file_bytes"]) and _is_int(files["max_model_bytes"]) \
            and files["large_file_bytes"] > files["max_model_bytes"]:
        errors.append("files.large_file_bytes cannot be larger than files.max_model_bytes")
    boolean("knowledge.offline")
    boolean("knowledge.verify_hash")
    positive_int("knowledge.max_results", 100)
    positive_int("knowledge.fetch_factor", 20)
    positive_int("knowledge.max_hit_chars", 20000)

    positive_int("kb.max_source_file_bytes")
    boolean("mcp.enabled")
    for name, agent in data["agents"].items():
        if not isinstance(agent, Mapping) or not isinstance(agent.get("enabled", True), bool):
            errors.append(f"agents.{name} must be a mapping with 'enabled: true/false'")
    errors.extend(_validate_memory(data["memory"]))
    return errors


def _validate_memory(memory: Mapping[str, Any]) -> list[str]:
    from champion.memory.storage import MemoryRefused
    from champion.memory.working import WorkingSettings

    errors = [f"memory.{key} must be true or false (got {memory[key]!r})"
              for key in ("enabled", "runtime_state", "summaries", "preferences") if not isinstance(memory[key], bool)]
    try:  # the same ranges the library checks
        WorkingSettings(max_turns=memory["max_turns"], max_tokens=memory["max_tokens"],
                        tool_output_chars=memory["tool_output_chars"], full_tool_turns=memory["full_tool_turns"])
    except MemoryRefused as exc:
        errors.append(f"memory.{str(exc).removeprefix('working memory: ')} (got "
                      f"{ {k: memory[k] for k in ('max_turns', 'max_tokens', 'tool_output_chars', 'full_tool_turns')} })")
    keep = memory["keep_archived"]
    if not _is_int(keep) or not 0 <= keep <= 10000:
        errors.append(f"memory.keep_archived must be a whole number from 0 to 10000 (got {keep!r})")
    if not isinstance(memory["user_root"], str) or not memory["user_root"]:
        errors.append("memory.user_root must be a folder path")
    if memory["project"] is not None and not (isinstance(memory["project"], str) and memory["project"]):
        errors.append("memory.project must be a folder path or null")
    return errors


# --------------------------------------------------------------- OpenAI key

def get_openai_api_key(root: Path | None = None,
                       env: Mapping[str, str] | None = None,
                       api_key_env: str = "OPENAI_API_KEY") -> tuple[str | None, str]:
    """Return ``(key, source)``; source is ``environment``, ``.env`` or ``missing``.

    The key is never logged or printed by Champion.
    """
    env = os.environ if env is None else env
    key = (env.get(api_key_env) or "").strip()
    if key:
        return key, "environment"
    dotenv_file = (root or project_root()) / ".env"
    if dotenv_file.is_file():
        from dotenv import dotenv_values  # reads the file, does not touch os.environ

        key = (dotenv_values(dotenv_file).get(api_key_env) or "").strip()
        if key:
            return key, ".env"
    return None, "missing"
