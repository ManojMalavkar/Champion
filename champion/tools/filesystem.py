"""Generic, read-only file tools: list a folder, read a text file, disk usage.

Each tool has two parts:
* a plain function (``list_folder``, ``read_text_file``, ``disk_usage``) that
  does the work and raises ``PolicyDenied`` / ``ToolError``; easy to test;
* a ``@function_tool`` that the model sees; it only passes the arguments to
  ``run_guarded`` (policy entry, level check, output cap, audit).

Every path goes through ``check_path`` and is opened with
``open_no_symlinks``. Files of ``files.large_file_bytes`` (100 MiB) or more
are not read here; the large-model tools of step 6 read them by keyword.
Ported and tightened from mChampion ``tools/typed_tools.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time

from agents import RunContextWrapper, function_tool

from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied, check_path, open_no_symlinks
from champion.policy.sensitive import name_reason
from champion.policy.tool_policy import ToolRule, register
from champion.tools.guard import ToolError, requires, run_guarded
from champion.tools.text_patch import file_sha256

MAX_LIST = 500
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE)
KEYWORD_LINE = re.compile(r"(?m)^\*[A-Za-z][A-Za-z0-9_+-]*")

register(ToolRule("filesystem_list", "read", "List the entries of a folder (names, types, sizes)"))
register(ToolRule("filesystem_read", "read", "Read a text file (start or from a line) and its SHA-256"))
register(ToolRule("file_hash", "read", "SHA-256 of a file of any size (streaming)"))
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


def _lines_from(stream, start_line: int, max_chars: int) -> tuple[bytes, int, int]:
    """Bytes of the lines from ``start_line`` (about ``max_chars`` characters), the last line
    number included, and the number of lines in the file (streaming)."""
    stream.seek(0)
    kept, size, last, total = [], 0, 0, 0
    for total, line in enumerate(stream, 1):
        if total >= start_line and size < max_chars * 4 and (not kept or size + len(line) <= max_chars * 4):
            kept.append(line)
            size += len(line)
            last = total
    return b"".join(kept), last, total


def read_text_file(context: ChampionContext, path: str, max_chars: int | None = None,
                   start_line: int | None = None) -> str:
    cap = context.config.get("execution.max_output_chars")
    max_chars = cap if max_chars is None else max_chars
    if not isinstance(max_chars, int) or not 1 <= max_chars <= cap:
        raise ToolError(f"max_chars must be between 1 and {cap}")
    if start_line is not None and (not isinstance(start_line, int) or start_line < 1):
        raise ToolError("start_line must be a line number (1 = first line)")
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
        large = context.config.get("files.large_file_bytes")
        if info.st_size >= large:
            raise ToolError(f"{file} is {human_size(info.st_size)}; files of {human_size(large)} or more "
                            "are read with the large-model tools (not available yet), not filesystem_read; "
                            "file_hash gives its SHA-256")
        head = stream.read(max(65536, max_chars * 4) + 1)
        if start_line is not None:
            data, last, total = _lines_from(stream, start_line, max_chars)
        digest = file_sha256(stream.fileno(), time.monotonic() + context.config.get("files.model_timeout_seconds"))
    head_text = head.decode("utf-8", errors="replace")
    text = head_text if start_line is None else data.decode("utf-8", errors="replace")
    for sample in (head_text, text):
        if "\x00" in sample:
            raise ToolError(f"{file} looks like a binary file; only text files can be read")
        if PRIVATE_KEY.search(sample):
            raise PolicyDenied(f"{file} contains a private key; private keys are always blocked")
    if file.suffix.lower() == ".key" and not KEYWORD_LINE.search(head_text[:65536]):
        raise PolicyDenied(f"{file} ends in .key but is not an LS-DYNA keyword file; other .key files are blocked")
    shown = text[:max_chars]
    if start_line is None:
        more = len(text) > max_chars or info.st_size > len(head)
        header = f"{file} ({human_size(info.st_size)}), first {len(shown)} characters"
        header += " (more not shown)" if more else " (whole file)"
    else:
        if start_line > total:
            raise ToolError(f"{file} has {total} line(s); start_line {start_line} is after the end")
        last = start_line + shown.count("\n") - (1 if shown.endswith("\n") else 0) if len(text) > max_chars else last
        header = f"{file} ({human_size(info.st_size)}), lines {start_line}-{last} of {total}"
    return f"{header}\nSHA-256 {digest}\n{shown}"


def hash_file(context: ChampionContext, path: str) -> str:
    """SHA-256 of a file of any size up to files.max_model_bytes (streaming, time limit)."""
    file = check_path(context.level, context.working_directory, path, "read")
    try:
        fd = open_no_symlinks(file, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ToolError(f"{file} does not exist") from None
    except OSError as exc:
        raise ToolError(f"cannot open {file} ({exc.strerror})") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ToolError(f"{file} is not a regular file")
        cap = context.config.get("files.max_model_bytes")
        if info.st_size > cap:
            raise ToolError(f"{file} is {human_size(info.st_size)}, above the safety cap of {human_size(cap)}")
        digest = file_sha256(fd, time.monotonic() + context.config.get("files.model_timeout_seconds"))
    finally:
        os.close(fd)
    return f"{file} ({human_size(info.st_size)}): SHA-256 {digest}"


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
async def filesystem_read(ctx: RunContextWrapper[ChampionContext], path: str, max_chars: int | None = None,
                          start_line: int | None = None) -> str:
    """Read a text file: the start, or from start_line on (up to max_chars characters).
    The result also gives the file's SHA-256 (needed as expected_sha256 to edit it).
    Files of 100 MiB or more are refused. A result starting with BLOCKED or ERROR explains why.

    Args:
        path: File to read; relative paths start at the working folder.
        max_chars: Maximum characters to return (default and maximum: the configured output limit).
        start_line: First line to show (1-based); leave out to read from the start.
    """
    return await run_guarded(ctx, "filesystem_read", {"path": path, "max_chars": max_chars, "start_line": start_line},
                             lambda: read_text_file(ctx.context, path, max_chars, start_line))


@function_tool(is_enabled=requires("read"))
async def file_hash(ctx: RunContextWrapper[ChampionContext], path: str) -> str:
    """SHA-256 of a file of any size (up to 8 GiB, streaming), for example a large LS-DYNA
    model before file_edit (pass it as expected_sha256).

    Args:
        path: File; relative paths start at the working folder.
    """
    return await run_guarded(ctx, "file_hash", {"path": path}, lambda: hash_file(ctx.context, path))


@function_tool(is_enabled=requires("read"))
async def filesystem_disk_usage(ctx: RunContextWrapper[ChampionContext], path: str = ".") -> str:
    """Show total, used and free space of the file system that holds a path.

    Args:
        path: Any path on the file system; default the working folder.
    """
    return await run_guarded(ctx, "filesystem_disk_usage", {"path": path},
                             lambda: disk_usage(ctx.context, path))


FILE_TOOLS = [filesystem_list, filesystem_read, filesystem_disk_usage]
