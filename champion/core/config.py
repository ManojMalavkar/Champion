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
* All other paths (storage, pydyna, kb) are relative to the project root: the
  parent of the ``config/`` folder that holds the file, or the file's own
  folder if it is not inside a ``config/`` folder.

The API key is never stored in the config. ``get_openai_api_key`` reads the
configured environment variable from the environment or a ``.env`` file in the
project root, without putting it into ``os.environ``.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

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
    "edit": {"full_backup": False},
    "storage": {"audit_db": "champion.db", "log_dir": "logs"},
    "agents": {"pydyna": {"enabled": True}, "linux_coder": {"enabled": True}},
    "pydyna": {
        "database_path": None,  # existing Chroma vector_db folder
        "keyword_reference_path": None,  # keyword_reference.jsonl
        "python_path": None,  # Python interpreter with ansys-dyna-core
        "large_file_bytes": 100 * MiB,
        "max_model_bytes": 8 * GiB,
        "max_include_files": 1024,
        "max_include_depth": 64,
        "model_timeout_seconds": 120,
        "max_memory_bytes": 4 * GiB,
    },
    "kb": {
        "sources": {"pydyna": None, "pydpf_post": None},
        "max_source_file_bytes": 2 * MiB,
    },
    "mcp": {"enabled": False, "servers": {}},
}

# Sections whose sub-keys are free (new agents, MCP servers, kb projects).
OPEN_SECTIONS = {"agents", "mcp.servers", "kb.sources"}

# Paths resolved against the project root (working_directory is special).
ROOT_PATH_KEYS = (
    "storage.audit_db",
    "storage.log_dir",
    "pydyna.database_path",
    "pydyna.keyword_reference_path",
    "pydyna.python_path",
    "access.file",
)


class ConfigError(ValueError):
    """Raised when the config file is missing, unreadable or invalid."""


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
    data = copy.deepcopy(DEFAULTS)
    warnings: list[str] = []
    if file is not None:
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {file}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{file} must contain a mapping at the top level")
        _merge(data, raw, "", warnings)

    root = project_root() if file is None else _base_dir(file)
    _normalise(data)
    _resolve_paths(data, root, working_directory)
    errors = validate(data)
    if errors:
        where = file or "defaults"
        raise ConfigError(f"Invalid configuration ({where}):\n- " + "\n- ".join(errors))
    return Config(data=data, path=file, root=root, warnings=warnings)


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

    for key in ("large_file_bytes", "max_model_bytes", "max_include_files", "max_include_depth",
                "model_timeout_seconds", "max_memory_bytes"):
        positive_int(f"pydyna.{key}")
    pyd = data["pydyna"]
    if _is_int(pyd["large_file_bytes"]) and _is_int(pyd["max_model_bytes"]) \
            and pyd["large_file_bytes"] > pyd["max_model_bytes"]:
        errors.append("pydyna.large_file_bytes cannot be larger than pydyna.max_model_bytes")

    positive_int("kb.max_source_file_bytes")
    boolean("mcp.enabled")
    for name, agent in data["agents"].items():
        if not isinstance(agent, Mapping) or not isinstance(agent.get("enabled", True), bool):
            errors.append(f"agents.{name} must be a mapping with 'enabled: true/false'")
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
