"""Verification tools for the Coder Agent (step 4d.6).

* ``code_check``: syntax check of Python, JSON, YAML and TOML files inside
  Champion. Nothing is executed (Python files are compiled, not run).
* ``run_check``: runs one of the checks the *user* listed in ``coder.checks``
  (for example ``tests: [/path/venv/bin/python, -m, pytest, -q]``). The agent
  can only pick a name, never a command; interpreters stay blocked for
  ``shell_run``. A check runs project code, so the user approves every run.
  It runs in the working folder with the executor's limits (clean
  environment, time, CPU, memory, output cap). Empty by default.
* ``git_info``: read-only Git status of the repository that holds a folder:
  branch, changed files, ``git diff --stat``. Git is optional; Champion's own
  ``/changes`` and ``/undo`` work without it. It never commits, checks out or
  discards anything. Repository settings that run programs (fsmonitor,
  external diff, textconv) are switched off, and no index is written.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import tomllib
import warnings
from pathlib import Path

import yaml
from agents import RunContextWrapper, function_tool

from champion.core.context import ChampionContext
from champion.execution.approval import ApprovalRequest, ask
from champion.execution.commands import Limits, resolve, run_steps
from champion.policy.scope import PolicyDenied, check_path, open_no_symlinks
from champion.policy.tool_policy import ToolRule, register
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError, requires, run_guarded

MAX_CHECK_FILES = 50
MAX_CHECK_BYTES = 4 * 1024 * 1024
KINDS = {".py": "python", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml"}
GIT_SAFE = ["git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c",
            "core.untrackedCache=false", "-c", "color.ui=false"]

register(ToolRule("code_check", "read", "Syntax check of Python, JSON, YAML and TOML files (nothing is run)"))
register(ToolRule("run_check", "edit", "Run a check the user configured in coder.checks (asks every time)"))
register(ToolRule("git_info", "read", "Read-only Git status and diff summary of a repository"))


# ------------------------------------------------------------------ code_check

def _check_text(kind: str, text: str, name: str) -> str | None:
    """The first syntax problem, or None."""
    try:
        if kind == "python":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compile(text, name, "exec", dont_inherit=True)  # compiles only, never runs
        elif kind == "json":
            json.loads(text)
        elif kind == "yaml":
            for _ in yaml.safe_load_all(text):
                pass
        else:
            tomllib.loads(text)
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    except json.JSONDecodeError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        return f"{where}{getattr(exc, 'problem', None) or exc}"
    except tomllib.TOMLDecodeError as exc:
        return str(exc)
    except (ValueError, RecursionError, MemoryError) as exc:
        return f"cannot be checked ({type(exc).__name__}: {exc})"
    return None


def check_files(context: ChampionContext, paths: list[str]) -> str:
    if not isinstance(paths, list) or not paths or len(paths) > MAX_CHECK_FILES:
        raise ToolError(f"paths must list 1 to {MAX_CHECK_FILES} files")
    rows, failed = [], 0
    for path in paths:
        target = check_path(context.level, context.working_directory, path, "read")
        kind = KINDS.get(target.suffix.lower())
        if kind is None:
            rows.append(f"SKIP   {target} (no checker for '{target.suffix or 'no extension'}'; checked: "
                        f"{', '.join(sorted(set(KINDS.values())))})")
            continue
        try:
            fd = open_no_symlinks(target, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            raise ToolError(f"{target} does not exist") from None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ToolError(f"{target} is not a regular file")
            if info.st_size > MAX_CHECK_BYTES:
                rows.append(f"SKIP   {target} ({human_size(info.st_size)}, above {human_size(MAX_CHECK_BYTES)})")
                continue
            data = stream.read()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            failed += 1
            rows.append(f"ERROR  {target} ({kind}): not UTF-8 at byte {exc.start}")
            continue
        problem = _check_text(kind, text, str(target))
        if problem:
            failed += 1
            rows.append(f"ERROR  {target} ({kind}): {problem}")
        else:
            rows.append(f"OK     {target} ({kind})")
    verdict = f"{failed} file(s) with errors" if failed else "no syntax errors"
    return f"code_check: {verdict} (syntax only; nothing was run)\n" + "\n".join(rows)


# ------------------------------------------------------------------ run_check

def _format(argv: list[str], result, limits: Limits) -> list[str]:
    lines = [f"$ {shlex.join(argv)}", f"exit code {result.exit_code}, {result.seconds:.1f} s"]
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
    return lines


def run_named_check(context: ChampionContext, name: str, audit: dict | None = None) -> str:
    audit = audit if audit is not None else {}
    if not context.level.allows("edit"):
        raise PolicyDenied(f"access level {context.level.name} does not allow running checks (they run project "
                           "code); a folder with edit access is needed")
    checks = context.config.get("coder.checks") or {}
    if not isinstance(name, str) or name not in checks:
        known = ", ".join(sorted(checks)) or "none"
        raise ToolError(f"there is no check named {name!r} (configured: {known}). Checks are set by the user in "
                        "config.yaml under coder.checks; use code_check for a syntax check")
    argv = [str(a) for a in checks[name]]
    folder = check_path(context.level, context.working_directory, ".", "read")
    executable = resolve(argv[0])
    audit.update({"check": name, "argv": argv})
    ask(context, ApprovalRequest("run_check", executable, tuple(argv), "check", str(folder), context.level.name,
                                 (str(folder),), note=f"runs the configured check '{name}' (project code)"))
    limits = Limits.from_config(context.config)
    result = run_steps([argv], str(folder), limits)
    audit.update({"exit_code": result.exit_code, "timed_out": result.timed_out})
    verdict = "PASSED" if result.exit_code == 0 and not result.timed_out else "FAILED"
    return "\n".join(_format(argv, result, limits) + [f"Check '{name}': {verdict}"])


# ------------------------------------------------------------------ git_info

def _repository(context: ChampionContext, path: str) -> Path:
    folder = check_path(context.level, context.working_directory, path, "read")
    if not folder.is_dir():
        folder = folder.parent
    for candidate in (folder, *folder.parents):
        if os.path.lexists(candidate / ".git"):
            check_path(context.level, context.working_directory, str(candidate), "read")
            return candidate
    raise ToolError(f"{folder} is not inside a Git repository (that is fine: /changes and /undo work without Git)")


def git_summary(context: ChampionContext, path: str = ".") -> str:
    root = _repository(context, path)
    limits = Limits.from_config(context.config)
    status = run_steps([GIT_SAFE + ["status", "--porcelain=v1", "--branch", "--ignore-submodules=all"]],
                       str(root), limits)
    if status.exit_code != 0:
        raise ToolError(f"git status failed in {root}: {(status.stderr or status.stdout).strip()[:500]}")
    diff = run_steps([GIT_SAFE + ["diff", "--stat", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"]],
                     str(root), limits)
    lines = status.stdout.splitlines()
    branch = lines[0][3:] if lines and lines[0].startswith("## ") else "unknown"
    changed = [line for line in lines[1:] if line.strip()]
    out = [f"Repository {root}, branch {branch}",
           f"{len(changed)} changed or untracked path(s)" + (":" if changed else " (clean)")]
    out += [f"  {line}" for line in changed[:100]]
    if len(changed) > 100:
        out.append(f"  ... and {len(changed) - 100} more")
    if diff.exit_code == 0 and diff.stdout.strip():
        out += ["git diff --stat:", diff.stdout.rstrip("\n")]
    out.append("Read-only: Champion never commits, checks out or discards changes.")
    return "\n".join(out)


# ------------------------------------------------------------------ tools

@function_tool(is_enabled=requires("read"))
async def code_check(ctx: RunContextWrapper[ChampionContext], paths: list[str]) -> str:
    """Syntax check of Python, JSON, YAML and TOML files after a change (nothing is executed).

    Args:
        paths: Up to 50 files; relative paths start at the working folder.
    """
    return await run_guarded(ctx, "code_check", {"paths": list(paths or [])[:MAX_CHECK_FILES]},
                             lambda: check_files(ctx.context, list(paths or [])))


@function_tool(is_enabled=requires("edit"))
async def run_check(ctx: RunContextWrapper[ChampionContext], name: str) -> str:
    """Run one of the checks the user configured (coder.checks in config.yaml, for example
    "tests"). You choose only the name; the command is the user's. The user approves every
    run. Use it after a change to run the project's tests.

    Args:
        name: Name of the configured check.
    """
    audit: dict = {"check": name}
    return await run_guarded(ctx, "run_check", audit, lambda: run_named_check(ctx.context, name, audit))


@function_tool(is_enabled=requires("read"))
async def git_info(ctx: RunContextWrapper[ChampionContext], path: str = ".") -> str:
    """Read-only Git summary of the repository that holds a folder: branch, changed and
    untracked files, git diff --stat. Never commits or discards anything.

    Args:
        path: A folder or file inside the repository; default the working folder.
    """
    return await run_guarded(ctx, "git_info", {"path": path}, lambda: git_summary(ctx.context, path))
