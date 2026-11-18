"""Read-only system tools (step 4a): processes and disk space by folder.

* ``process_list``: processes sorted by memory or CPU, read from ``/proc``
  (Linux). By default only the user's own processes. Only the command
  *name* is shown, never the arguments or the environment: command lines can
  contain tokens or passwords, also of other users' programs.
* ``disk_analysis``: largest folders (size including sub-folders) and largest
  files below a folder, with the same safe walk as ``filesystem_find``
  (no links followed, blocked entries skipped, about 10 s). Hard links are
  counted once.

``/proc`` stays blocked for the file tools; ``process_list`` is trusted code
that reads only the few fields it shows.
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from agents import RunContextWrapper, function_tool

from champion.core.context import ChampionContext
from champion.policy.scope import check_path
from champion.policy.tool_policy import ToolRule, register
from champion.tools.filesystem import human_size
from champion.tools.guard import ToolError, requires, run_guarded
from champion.tools.search import check_limit
from champion.tools.walk import WalkStats, check_depth, walk

PROC = Path("/proc")
MAX_ROWS = 200

register(ToolRule("process_list", "read", "Processes sorted by memory or CPU (command names only)"))
register(ToolRule("disk_analysis", "read", "Largest folders and files below a folder"))


@dataclass
class Process:
    pid: int
    uid: int
    command: str
    state: str
    rss_bytes: int
    cpu_percent: float


def _user(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def read_processes(proc: Path = PROC) -> list[Process]:
    """Parse ``/proc/<pid>/stat`` and ``status``; processes that end meanwhile are skipped."""
    if not (proc / "uptime").is_file():
        raise ToolError("process_list needs Linux /proc; it is not available on this system")
    uptime = float((proc / "uptime").read_text().split()[0])
    ticks = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    result = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text()
            status = (entry / "status").read_text()
        except OSError:
            continue
        start, end = stat_text.find("("), stat_text.rfind(")")
        if start < 0 or end < 0:
            continue
        command = stat_text[start + 1:end]
        fields = stat_text[end + 2:].split()
        try:
            state, utime, stime = fields[0], int(fields[11]), int(fields[12])
            started, rss_pages = int(fields[19]), int(fields[21])
        except (IndexError, ValueError):
            continue
        uid = next((int(line.split()[1]) for line in status.splitlines() if line.startswith("Uid:")), -1)
        alive = max(uptime - started / ticks, 1e-6)
        cpu = 100.0 * (utime + stime) / ticks / alive
        result.append(Process(int(entry.name), uid, command, state, rss_pages * page, cpu))
    return result


def list_processes(context: ChampionContext, sort: str = "memory", limit: int = 20, all_users: bool = False,
                   proc: Path = PROC) -> str:
    if sort not in ("memory", "cpu"):
        raise ToolError("sort must be memory or cpu")
    limit = check_limit(limit, "limit", MAX_ROWS)
    processes = read_processes(proc)
    me = os.getuid()
    if not all_users:
        processes = [p for p in processes if p.uid == me]
    key = (lambda p: (-p.rss_bytes, p.pid)) if sort == "memory" else (lambda p: (-p.cpu_percent, p.pid))
    processes.sort(key=key)
    shown = processes[:limit]
    who = "all users" if all_users else f"user {_user(me)}"
    lines = [f"Processes of {who}, sorted by {sort}: {len(shown)} of {len(processes)} shown. "
             "Command names only: arguments and environment are not shown (they can contain secrets). "
             "CPU% is the average since the process started.",
             f"{'PID':>8}  {'USER':<12} {'MEMORY':>10}  {'CPU%':>6}  STATE  COMMAND"]
    for p in shown:
        lines.append(f"{p.pid:>8}  {_user(p.uid)[:12]:<12} {human_size(p.rss_bytes):>10}  "
                     f"{p.cpu_percent:>6.1f}  {p.state:<5}  {p.command}")
    return "\n".join(lines)


def analyse_disk(context: ChampionContext, path: str = ".", top: int = 10, max_depth: int = 10) -> str:
    start = check_path(context.level, context.working_directory, path, "read")
    top = check_limit(top, "top", 100)
    depth = check_depth(max_depth)
    stats = WalkStats()
    folders: dict[str, int] = {}
    files: list[tuple[int, str]] = []
    seen: set[tuple[int, int]] = set()
    total = file_count = 0
    for entry in walk(start, max_depth=depth, stats=stats):
        if entry.kind == "dir":
            folders.setdefault(entry.relative, 0)
            continue
        if entry.kind != "file" or entry.inode in seen:
            continue
        seen.add(entry.inode)
        total += entry.size
        file_count += 1
        files.append((entry.size, entry.relative))
        parts = entry.relative.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folder = "/".join(parts[:i])
            folders[folder] = folders.get(folder, 0) + entry.size
    biggest_folders = sorted(folders.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    biggest_files = sorted(files, key=lambda f: (-f[0], f[1]))[:top]
    lines = [f"{start}: {human_size(total)} in {file_count} file(s) and {len(folders)} folder(s), "
             f"counted down to depth {depth}", stats.summary()]
    lines.append("Largest folders (size including sub-folders):")
    lines += [f"  {human_size(size):>10}  {name}/" for name, size in biggest_folders] or ["  (no sub-folders)"]
    lines.append("Largest files:")
    lines += [f"  {human_size(size):>10}  {name}" for size, name in biggest_files] or ["  (no files)"]
    return "\n".join(lines)


@function_tool(is_enabled=requires("read"))
async def process_list(ctx: RunContextWrapper[ChampionContext], sort: str = "memory", limit: int = 20,
                       all_users: bool = False) -> str:
    """List running processes sorted by memory or CPU: PID, user, memory, CPU%, state and
    command name (never arguments or environment).

    Args:
        sort: "memory" (default) or "cpu" (average CPU% since start).
        limit: Number of processes to show (1-200, default 20).
        all_users: Include other users' processes (default: only the user's own).
    """
    arguments = {"sort": sort, "limit": limit, "all_users": all_users}
    return await run_guarded(ctx, "process_list", arguments, lambda: list_processes(ctx.context, **arguments))


@function_tool(is_enabled=requires("read"))
async def disk_analysis(ctx: RunContextWrapper[ChampionContext], path: str = ".", top: int = 10,
                        max_depth: int = 10) -> str:
    """Show which folders and files use the most space below a folder (sizes include
    sub-folders; links not followed; blocked entries skipped; stops after about 10 s).

    Args:
        path: Folder to analyse; relative paths start at the working folder.
        top: How many folders and files to show (1-100, default 10).
        max_depth: Deepest sub-folder level to count (1-50, default 10).
    """
    arguments = {"path": path, "top": top, "max_depth": max_depth}
    return await run_guarded(ctx, "disk_analysis", arguments, lambda: analyse_disk(ctx.context, **arguments))


SYSTEM_TOOLS = [process_list, disk_analysis]
