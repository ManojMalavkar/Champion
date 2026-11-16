"""Generic, read-only file tools: list a folder, read a text file, disk usage.

Each tool has two parts:
* a plain function (``list_folder``, ``read_text_file``, ``disk_usage``) that
  does the work and raises ``PolicyDenied`` / ``ToolError``; easy to test;
* a ``@function_tool`` that the model sees; it only passes the arguments to
  ``run_guarded`` (policy entry, level check, output cap, audit).

Every path goes through ``check_path`` and is opened with
``open_no_symlinks``. Files of ``pydyna.large_file_bytes`` (100 MiB) or more
are not read here; the large-model tools of step 6 read them by keyword.
Ported and tightened from mChampion ``tools/typed_tools.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import stat

from agents import RunContextWrapper, function_tool

from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied, check_path, open_no_symlinks
from champion.policy.sensitive import name_reason
from champion.policy.tool_policy import ToolRule, register
from champion.tools.guard import ToolError, requires, run_guarded

MAX_LIST = 500
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE)
KEYWORD_LINE = re.compile(r"(?m)^\*[A-Za-z][A-Za-z0-9_+-]*")

register(ToolRule("filesystem_list", "read", "List the entries of a folder (names, types, sizes)"))
register(ToolRule("filesystem_read", "read", "Read the start of a text file"))
register(ToolRule("filesystem_disk_usage", "read", "Total, used and free space of a file system"))


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def list_folder(context: ChampionContext, path: str = ".", limit: int = 200) -> str:
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIST:
        raise ToolError(f"limit must be between 1 and {MAX_LIST}")
    folder = check_path(context.level, context.working_directory, path, "read")
    try:
        fd = open_no_symlinks(folder, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        raise ToolError(f"{folder} does not exist") from None
    except NotADirectoryError:
        raise ToolError(f"{folder} is not a folder") from None
    except OSError as exc:
        raise ToolError(f"cannot open {folder} ({exc.strerror})") from None
    rows, hidden = [], 0
    try:
        with os.scandir(fd) as entries:
            items = sorted(entries, key=lambda e: (e.name.casefold(), e.name))
        for entry in items:
            if name_reason(entry.name):
                hidden += 1
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                rows.append(f"link  {'-':>10}  {entry.name} (symbolic link, not followed)")
            elif stat.S_ISDIR(info.st_mode):
                rows.append(f"dir   {'-':>10}  {entry.name}/")
            elif stat.S_ISREG(info.st_mode):
                rows.append(f"file  {human_size(info.st_size):>10}  {entry.name}")
            else:
                rows.append(f"other {'-':>10}  {entry.name}")
    finally:
        os.close(fd)
    shown = rows[:limit]
    header = f"{folder}: {len(rows)} entries"
    if hidden:
        header += f", {hidden} hidden (secret-looking names)"
    if len(rows) > limit:
        header += f", first {limit} shown"
    return "\n".join([header, *shown]) if shown else header


def read_text_file(context: ChampionContext, path: str, max_chars: int | None = None) -> str:
    cap = context.config.get("execution.max_output_chars")
    max_chars = cap if max_chars is None else max_chars
    if not isinstance(max_chars, int) or not 1 <= max_chars <= cap:
        raise ToolError(f"max_chars must be between 1 and {cap}")
    file = check_path(context.level, context.working_directory, path, "read")
    try:
        fd = open_no_symlinks(file, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ToolError(f"{file} does not exist") from None
    except OSError as exc:
        raise ToolError(f"cannot open {file} ({exc.strerror})") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ToolError(f"{file} is not a regular file")
        large = context.config.get("pydyna.large_file_bytes")
        if info.st_size >= large:
            raise ToolError(f"{file} is {human_size(info.st_size)}; files of {human_size(large)} or more "
                            "are read with the large-model tools (not available yet), not filesystem_read")
        data = stream.read(max(65536, max_chars * 4) + 1)
    text = data.decode("utf-8", errors="replace")
    if "\x00" in text:
        raise ToolError(f"{file} looks like a binary file; only text files can be read")
    if PRIVATE_KEY.search(text):
        raise PolicyDenied(f"{file} contains a private key; private keys are always blocked")
    if file.suffix.lower() == ".key" and not KEYWORD_LINE.search(text[:65536]):
        raise PolicyDenied(f"{file} ends in .key but is not an LS-DYNA keyword file; other .key files are blocked")
    shown = text[:max_chars]
    more = len(text) > max_chars or info.st_size > len(data)
    header = f"{file} ({human_size(info.st_size)}), first {len(shown)} characters"
    header += " (more not shown)" if more else " (whole file)"
    return f"{header}\n{shown}"


def disk_usage(context: ChampionContext, path: str = ".") -> str:
    target = check_path(context.level, context.working_directory, path, "read")
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        raise ToolError(f"cannot read disk usage of {target} ({exc.strerror})") from None
    percent = 100 * usage.used / usage.total if usage.total else 0
    return (f"{target}: total {human_size(usage.total)}, used {human_size(usage.used)} ({percent:.0f}%), "
            f"free {human_size(usage.free)}")


@function_tool(is_enabled=requires("read"))
async def filesystem_list(ctx: RunContextWrapper[ChampionContext], path: str = ".", limit: int = 200) -> str:
    """List the entries of a folder: type (file, dir, link), size and name.

    Args:
        path: Folder to list; relative paths start at the working folder.
        limit: Maximum number of entries to show (1-500).
    """
    return await run_guarded(ctx, "filesystem_list", {"path": path, "limit": limit},
                             lambda: list_folder(ctx.context, path, limit))


@function_tool(is_enabled=requires("read"))
async def filesystem_read(ctx: RunContextWrapper[ChampionContext], path: str, max_chars: int | None = None) -> str:
    """Read the start of a text file (up to max_chars characters). Files of 100 MiB
    or more are refused. A result starting with BLOCKED or ERROR explains why.

    Args:
        path: File to read; relative paths start at the working folder.
        max_chars: Maximum characters to return (default and maximum: the configured output limit).
    """
    return await run_guarded(ctx, "filesystem_read", {"path": path, "max_chars": max_chars},
                             lambda: read_text_file(ctx.context, path, max_chars))


@function_tool(is_enabled=requires("read"))
async def filesystem_disk_usage(ctx: RunContextWrapper[ChampionContext], path: str = ".") -> str:
    """Show total, used and free space of the file system that holds a path.

    Args:
        path: Any path on the file system; default the working folder.
    """
    return await run_guarded(ctx, "filesystem_disk_usage", {"path": path},
                             lambda: disk_usage(ctx.context, path))


FILE_TOOLS = [filesystem_list, filesystem_read, filesystem_disk_usage]
