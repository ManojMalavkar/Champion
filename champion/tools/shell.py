"""Shell tools (step 4a, PLAN 4a.5): ``shell_run`` and ``shell_pipeline``.

Thin wrappers: policy (``command_policy``) -> approval for deletes
(``approval``) -> executor (``commands``) -> audit row + change list. They
never take a shell string, only argument lists.

* ``shell_run``: one command. Read commands run at every level; write commands
  (``mkdir cp mv touch tar gzip gunzip chmod``) need a write level and run
  without a prompt, each written path goes into the change list (no undo for
  shell writes in step 4; ``file_edit`` and ``files_organize`` can be
  undone); ``rm`` needs a write level and the user's yes every time.
* ``shell_pipeline``: 2 to 5 read-only commands, the output of each going to
  the next (like ``du -a . | sort -n | tail``), still without a shell.

The audit row of each call has the expanded argument list, the class, the
affected paths, the exit code, the time and the output size; never the
command output itself.
"""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

from champion.core.context import ChampionContext
from champion.execution.approval import ApprovalRequest, ask
from champion.execution.command_policy import CommandCheck, check_command
from champion.execution.commands import CommandResult, Limits, resolve, run_steps
from champion.policy.scope import PolicyDenied, check_path
from champion.policy.tool_policy import ToolRule, register
from champion.tools.changes import agent_name, new_group, record
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError, requires, run_guarded

MAX_STEPS = 5
MAX_AUDIT_PATHS = 50

register(ToolRule("shell_run", "read", "Run one allowed command (argument list, no shell)"))
register(ToolRule("shell_pipeline", "read", "Run 2-5 read-only commands connected by pipes (no shell)"))


class Step(BaseModel):
    argv: list[str]


def _state(path: Path) -> tuple[bool, int | None]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False, None
    return True, info.st_size if stat.S_ISREG(info.st_mode) else None


def _action(check: CommandCheck, existed: bool) -> str:
    if check.command_class == "delete":
        return "deleted"
    command, argv = check.command, check.argv
    if command == "gzip":
        return "extracted" if {"-d", "--decompress"} & set(argv) else "compressed"
    if command == "tar":
        return "created" if any(a in ("--create",) or (a.startswith("-") and not a.startswith("--") and "c" in a)
                                for a in argv[1:]) else "extracted"
    if command == "touch":
        return "modified" if existed else "created"
    return {"mkdir": "created", "cp": "copied", "mv": "moved", "gunzip": "extracted",
            "chmod": "permissions"}.get(command, "modified")


def _record_changes(context: ChampionContext, check: CommandCheck, before: dict, result: CommandResult,
                    tool: str, agent: str) -> list[int]:
    numbers, group = [], new_group()
    detail = shlex.join(check.argv)[:300]
    for path in check.writes + check.deletes:
        existed, size_before = before[path]
        exists, size_after = _state(path)
        if result.exit_code != 0 and (existed, size_before) == (exists, size_after):
            continue
        numbers.append(record(context, tool=tool, action=_action(check, existed), path=path, agent=agent,
                              size_before=size_before, size_after=size_after, detail=detail, group_id=group))
    return [n for n in numbers if n]


def _format(steps: list[list[str]], result: CommandResult, limits: Limits, numbers: list[int]) -> str:
    shown = " | ".join(shlex.join(argv) for argv in steps)
    codes = ", ".join(str(c) for c in result.exit_codes)
    lines = [f"$ {shown}", f"exit code {result.exit_code}" + (f" (steps: {codes})" if len(steps) > 1 else "")
             + f", {result.seconds:.1f} s"]
    if result.stdout:
        lines.append(result.stdout.rstrip("\n"))
    if result.stderr:
        lines += ["[stderr]", result.stderr]
    if result.truncated:
        lines.append(f"[output cut: {human_size(result.output_bytes)} produced, first {limits.output_chars} "
                     "characters shown; narrow the command]")
    if result.timed_out:
        lines.append(f"[stopped at the time limit of {limits.timeout_seconds:.0f} s; the command and its "
                     "children were killed]")
    if result.redacted:
        lines.append("[private keys or tokens in the output were hidden by Champion]")
    if numbers:
        span = f"#{numbers[0]}" if len(numbers) == 1 else f"#{numbers[0]}-#{numbers[-1]}"
        lines.append(f"Changed: {len(numbers)} path(s), {span} in /changes (shell changes cannot be undone)")
    return "\n".join(lines)


def _folder(context: ChampionContext, cwd: str) -> Path:
    folder = check_path(context.level, context.working_directory, cwd or ".", "read")
    if not os.path.isdir(folder):
        raise ToolError(f"{folder} is not a folder")
    return folder


def _audit_paths(check: CommandCheck) -> list[str]:
    return [str(p) for p in (check.writes + check.deletes + check.reads)][:MAX_AUDIT_PATHS]


def run_shell(context: ChampionContext, argv: list[str], cwd: str = ".", *, tool: str = "shell_run",
              agent: str = "", audit: dict | None = None) -> str:
    audit = audit if audit is not None else {}
    folder = _folder(context, cwd)
    check = check_command(argv, context.level, context.working_directory, folder)
    audit.update({"argv": check.argv, "class": check.command_class, "paths": _audit_paths(check)})
    executable = resolve(check.command)
    if check.needs_approval:
        request = ApprovalRequest.for_command(tool, check, executable, context.level.name)
        ask(context, request)
        again = check_command(argv, context.level, context.working_directory, folder)
        latest = ApprovalRequest.for_command(tool, again, resolve(again.command), context.level.name)
        if latest.fingerprint != request.fingerprint:
            raise PolicyDenied("the command changed between the approval and the start (other paths, program or "
                               "level); nothing was run")
        audit["approved"] = request.fingerprint[:16]
    limits = Limits.from_config(context.config)
    before = {path: _state(path) for path in check.writes + check.deletes}
    result = run_steps([check.argv], str(folder), limits)
    audit.update({"exit_code": result.exit_code, "timed_out": result.timed_out, "output_bytes": result.output_bytes})
    numbers = _record_changes(context, check, before, result, tool, agent)
    return _format([check.argv], result, limits, numbers)


def run_pipeline(context: ChampionContext, steps: list, cwd: str = ".", *, audit: dict | None = None) -> str:
    audit = audit if audit is not None else {}
    argvs = [s.argv if isinstance(s, Step) else s["argv"] if isinstance(s, dict) else s for s in steps or []]
    if not 2 <= len(argvs) <= MAX_STEPS:
        raise ToolError(f"a pipeline has 2 to {MAX_STEPS} steps; use shell_run for one command")
    folder = _folder(context, cwd)
    checks = [check_command(argv, context.level, context.working_directory, folder) for argv in argvs]
    audit.update({"steps": [c.argv for c in checks], "class": "read",
                  "paths": [p for c in checks for p in _audit_paths(c)][:MAX_AUDIT_PATHS]})
    for number, check in enumerate(checks, start=1):
        if check.command_class != "read":
            raise PolicyDenied(f"step {number} ({check.command}) is a {check.command_class} command; pipelines are "
                               "read-only, run it alone with shell_run")
    limits = Limits.from_config(context.config)
    result = run_steps([c.argv for c in checks], str(folder), limits)
    audit.update({"exit_code": result.exit_code, "timed_out": result.timed_out, "output_bytes": result.output_bytes})
    return _format([c.argv for c in checks], result, limits, [])


COMMANDS_HELP = ("read: ls find du df stat file head tail wc grep sort uniq cut diff md5sum sha256sum ps free uptime "
                 "nproc lscpu nvidia-smi; write (write level, listed in /changes): mkdir touch cp mv tar gzip gunzip "
                 "chmod; delete (the user must approve every time): rm")


@function_tool(is_enabled=requires("read"))
async def shell_run(ctx: RunContextWrapper[ChampionContext], argv: list[str], cwd: str = ".") -> str:
    """Run ONE allowed Linux command, given as an argument list (no shell: no pipes,
    redirection, variables or command substitution). Wildcards such as *.k in file arguments
    are expanded by Champion. Prefer the structured tools (filesystem_find, filesystem_grep,
    disk_analysis, process_list, file_edit, files_organize); use this only when they cannot do
    the job. Allowed commands: read: ls find du df stat file head tail wc grep sort uniq cut
    diff md5sum sha256sum ps free uptime nproc lscpu nvidia-smi; write (write level, listed in
    /changes): mkdir touch cp mv tar gzip gunzip chmod; delete (the user approves every time):
    rm. A BLOCKED or not-approved command must not be retried in another form.

    Args:
        argv: The command and its arguments, for example ["find", ".", "-name", "*.dyn"].
        cwd: Folder to run in; relative paths start at the working folder.
    """
    arguments = {"argv": argv, "cwd": cwd}
    return await run_guarded(ctx, "shell_run", arguments,
                             lambda: run_shell(ctx.context, argv, cwd, agent=agent_name(ctx), audit=arguments))


@function_tool(is_enabled=requires("read"))
async def shell_pipeline(ctx: RunContextWrapper[ChampionContext], steps: list[Step], cwd: str = ".") -> str:
    """Run 2 to 5 read-only commands where the output of each goes to the next, like
    `du -a . | sort -n | tail -n 10`, without a shell. Every step must be a read command
    (see shell_run). Returns the output of the last step.

    Args:
        steps: The commands in order, for example
            [{"argv": ["du", "-a", "."]}, {"argv": ["sort", "-n"]}, {"argv": ["tail", "-n", "10"]}].
        cwd: Folder to run in; relative paths start at the working folder.
    """
    arguments = {"steps": [s.argv for s in steps], "cwd": cwd}
    return await run_guarded(ctx, "shell_pipeline", arguments,
                             lambda: run_pipeline(ctx.context, steps, cwd, audit=arguments))


SHELL_TOOLS = [shell_run, shell_pipeline]
