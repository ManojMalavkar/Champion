"""``champion doctor``: check that Champion is ready to run.

Each check returns a ``CheckResult`` with status OK, WARN or FAIL. Checks never
raise and never print the OpenAI key. ``champion doctor`` exits with code 1 when
any check fails, so it can also be used in scripts.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable, Mapping

from champion.core.config import PLACEHOLDER_MODEL, Config, get_openai_api_key

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# (import name, distribution name, needed for)
REQUIRED_PACKAGES = [
    ("agents", "openai-agents", "agents"),
    ("openai", "openai", "OpenAI client"),
    ("yaml", "PyYAML", "config"),
    ("dotenv", "python-dotenv", ".env key file"),
    ("rich", "rich", "terminal output"),
    ("textual", "textual", "terminal UI (step 5)"),
]
OPTIONAL_PACKAGES = [
    ("chromadb", "chromadb", "PyDyna knowledge base (step 3, extra 'pydyna')"),
    ("sentence_transformers", "sentence-transformers", "embeddings (step 3, extra 'pydyna')"),
]


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "?"


def check_python() -> CheckResult:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 10):
        return CheckResult("Python", FAIL, f"{version}; Champion needs Python 3.10 or newer")
    return CheckResult("Python", OK, f"{version} ({sys.executable})")


def check_config(config: Config) -> list[CheckResult]:
    results = []
    if config.path is None:
        results.append(CheckResult("Config file", WARN,
                                   "none found, using defaults (copy config/config.example.yaml to config/config.yaml)"))
    else:
        results.append(CheckResult("Config file", OK, str(config.path)))
    for warning in config.warnings:
        results.append(CheckResult("Config key", WARN, warning))
    model = config.get("llm.model_id")
    if model == PLACEHOLDER_MODEL:
        results.append(CheckResult("Model", FAIL, "set llm.model_id in config.yaml"))
    else:
        fallback = config.get("llm.model_ids") or []
        extra = f", fallback: {', '.join(fallback)}" if fallback else ""
        results.append(CheckResult("Model", OK, f"{model}{extra}"))
    level = config.get("policy.default_level")
    results.append(CheckResult("Default access level", OK, level))
    tracing = config.get("llm.tracing")
    results.append(CheckResult("SDK tracing", WARN if tracing else OK,
                               "on: traces are sent to OpenAI" if tracing else "off"))
    return results


def check_api_key(config: Config, env: Mapping[str, str] | None = None) -> list[CheckResult]:
    api_key_env = config.get("llm.api_key_env")
    key, source = get_openai_api_key(config.root, env, api_key_env)
    if key is None:
        return [CheckResult("OpenAI key", FAIL,
                            f"{api_key_env} not found in the environment or {config.root / '.env'}")]
    results = [CheckResult("OpenAI key", OK, f"found in {source} (value not shown)")]
    dotenv_file = config.root / ".env"
    if source == ".env" and os.name == "posix":
        mode = stat.S_IMODE(dotenv_file.stat().st_mode)
        if mode & 0o077:
            results.append(CheckResult(".env permissions", WARN,
                                       f"{oct(mode)}: readable by others; run chmod 600 {dotenv_file}"))
    return results


def check_packages(find_spec: Callable = importlib.util.find_spec) -> list[CheckResult]:
    results = []
    for module, dist, purpose in REQUIRED_PACKAGES:
        if find_spec(module) is None:
            results.append(CheckResult(f"Package {dist}", FAIL, f"missing (needed for {purpose}); pip install -e ."))
        else:
            results.append(CheckResult(f"Package {dist}", OK, _version(dist)))
    for module, dist, purpose in OPTIONAL_PACKAGES:
        if find_spec(module) is None:
            results.append(CheckResult(f"Package {dist}", WARN, f"not installed (optional: {purpose})"))
        else:
            results.append(CheckResult(f"Package {dist}", OK, _version(dist)))
    return results


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def check_paths(config: Config) -> list[CheckResult]:
    results = []
    work = Path(config.get("execution.working_directory"))
    if work.is_dir():
        results.append(CheckResult("Working directory", OK, str(work)))
    else:
        results.append(CheckResult("Working directory", FAIL, f"not a folder: {work}"))

    for key, label in (("storage.audit_db", "Audit database"), ("storage.log_dir", "Log folder")):
        target = Path(config.get(key))
        parent = _nearest_existing(target if key == "storage.log_dir" else target.parent)
        if os.access(parent, os.W_OK):
            results.append(CheckResult(label, OK, f"{target} (writable)"))
        else:
            results.append(CheckResult(label, FAIL, f"{target}: cannot write in {parent}"))

    db = config.get("pydyna.database_path")
    if not db:
        results.append(CheckResult("PyDyna vector DB", WARN, "pydyna.database_path not set (needed from step 3)"))
    elif not (Path(db) / "chroma.sqlite3").is_file():
        results.append(CheckResult("PyDyna vector DB", WARN, f"no chroma.sqlite3 in {db}"))
    else:
        results.append(CheckResult("PyDyna vector DB", OK, db))

    ref = config.get("pydyna.keyword_reference_path")
    if not ref:
        results.append(CheckResult("Keyword reference", WARN, "pydyna.keyword_reference_path not set (needed from step 3)"))
    elif not Path(ref).is_file():
        results.append(CheckResult("Keyword reference", WARN, f"file not found: {ref}"))
    else:
        results.append(CheckResult("Keyword reference", OK, ref))

    python = config.get("pydyna.python_path")
    if not python:
        results.append(CheckResult("PyDyna Python", WARN, "pydyna.python_path not set (needed from step 8)"))
    elif not (Path(python).is_file() and os.access(python, os.X_OK)):
        results.append(CheckResult("PyDyna Python", WARN, f"not an executable file: {python}"))
    else:
        results.append(CheckResult("PyDyna Python", OK, python))
    return results


def check_online(config: Config, env: Mapping[str, str] | None = None) -> list[CheckResult]:
    """Check the primary model and configured alternatives with Responses API calls."""
    key, _ = get_openai_api_key(config.root, env, config.get("llm.api_key_env"))
    if key is None:
        return [CheckResult("OpenAI connection", FAIL, "skipped: no key")]

    models = list(dict.fromkeys([
        config.get("llm.model_id"),
        *(config.get("llm.model_ids") or []),
    ]))
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=config.get("llm.base_url"), timeout=15, max_retries=0)
    except Exception as exc:  # report the error type only; messages may echo request details
        status = getattr(exc, "status_code", None)
        code = f" (HTTP {status})" if status else ""
        return [CheckResult("OpenAI connection", FAIL, f"{type(exc).__name__}{code}")]

    results = []
    for model in models:
        try:
            client.responses.create(
                model=model,
                max_output_tokens=16,
                input=[{"role": "user", "content": [{"type": "input_text", "text": "Reply with OK."}]}],
            )
            results.append(CheckResult(f"Model {model}", OK, "Responses API succeeded"))
        except Exception as exc:  # report the error type only; messages may echo request details
            status = getattr(exc, "status_code", None)
            code = f" (HTTP {status})" if status else ""
            results.append(CheckResult(f"Model {model}", FAIL, f"{type(exc).__name__}{code}"))
    return results


def run_checks(config: Config, *, online: bool = False,
               env: Mapping[str, str] | None = None) -> list[CheckResult]:
    results = [check_python()]
    for check in (lambda: check_config(config), lambda: check_api_key(config, env),
                  check_packages, lambda: check_paths(config)):
        try:
            results.extend(check())
        except Exception as exc:  # a broken check must not hide the others
            results.append(CheckResult("Doctor", FAIL, f"check crashed: {type(exc).__name__}: {exc}"))
    if online:
        results.extend(check_online(config, env))
    return results


def render(results: list[CheckResult]) -> None:
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    colours = {OK: "green", WARN: "yellow", FAIL: "red"}
    table = Table(title="Champion doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for result in results:
        table.add_row(escape(result.name), f"[{colours[result.status]}]{result.status}[/]", escape(result.detail))
    console = Console()
    console.print(table)
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    console.print(f"{fails} failed, {warns} warnings")
