"""Read-only search tools (step 4a): find files by name, find text inside files.

* ``filesystem_find``: name patterns (``*.k``, ``*.dyn``), type, size and age
  filters, sorting and a result limit.
* ``filesystem_grep``: plain text (default) or a regular expression inside
  text files; reports file, line number and the matching line.

Both walk with ``walk.py`` (no links followed, blocked entries skipped and
counted, about 10 s per call) after ``check_path`` on the start folder, so
they follow the access level exactly like the step 2 file tools.

``filesystem_grep`` skips binary files, files of ``pydyna.large_file_bytes``
(100 MiB) or more, files with a private key, and ``.key`` files that are not
LS-DYNA keyword files. Plain text is the default because LS-DYNA keywords such
as ``*MAT_024`` contain ``*``, which a regular expression would misread.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path

from agents import RunContextWrapper, function_tool

from champion.core.context import ChampionContext
from champion.policy.scope import check_path, open_no_symlinks
from champion.policy.tool_policy import ToolRule, register
from champion.tools.filesystem import KEYWORD_LINE, PRIVATE_KEY, human_size
from champion.tools.guard import ToolError, requires, run_guarded
from champion.tools.walk import WALK_SECONDS, WalkStats, check_depth, walk

MAX_LIMIT = 500
MAX_COLLECT = 100_000
MAX_PATTERNS = 20
MAX_MATCHES = 1000
MAX_LINE_SHOWN = 200
MAX_REGEX_LINE = 4096
LINE_PIECE = 65536
TYPES = {"file": "file", "f": "file", "files": "file", "folder": "dir", "dir": "dir", "d": "dir",
         "directory": "dir", "folders": "dir", "any": "any", "all": "any"}
SORTS = ("name", "size", "modified")
SIZE_TEXT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)
UNITS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}

register(ToolRule("filesystem_find", "read", "Find files or folders by name, type, size and age"))
register(ToolRule("filesystem_grep", "read", "Find text inside text files (file, line, matching line)"))


def parse_size(value, name: str) -> int | None:
    """Bytes from 1234, "100M", "1.5 GiB" ... (binary units); None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ToolError(f"{name} must be a size such as 1048576, '100M' or '2G'")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ToolError(f"{name} cannot be negative")
        return int(value)
    match = SIZE_TEXT.match(str(value))
    if not match:
        raise ToolError(f"{name} must be a size such as 1048576, '100M' or '2G' (got {value!r})")
    return int(float(match.group(1)) * UNITS[match.group(2).lower()])


def check_patterns(patterns) -> list[str]:
    if patterns is None or patterns == []:
        return []
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or len(patterns) > MAX_PATTERNS:
        raise ToolError(f"patterns must be a list of at most {MAX_PATTERNS} name patterns")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 100:
            raise ToolError("each pattern must be a non-empty name pattern of at most 100 characters")
        if "/" in pattern or ".." in pattern or "\x00" in pattern:
            raise ToolError(f"pattern {pattern!r} must be a file name pattern such as '*.k' (no '/' or '..'); "
                            "use path for the folder")
    return [p.strip() for p in patterns]


def name_matches(name: str, patterns: list[str], case_sensitive: bool) -> bool:
    if not patterns:
        return True
    if case_sensitive:
        return any(fnmatch.fnmatchcase(name, p) for p in patterns)
    lower = name.lower()
    return any(fnmatch.fnmatchcase(lower, p.lower()) for p in patterns)


def check_limit(value: int, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ToolError(f"{name} must be between 1 and {maximum}")
    return value


def _stamp(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ find

def find_files(context: ChampionContext, path: str = ".", patterns: list[str] | None = None,
               recursive: bool = True, max_depth: int = 10, type: str = "file",
               min_size=None, max_size=None, modified_within_days: float | None = None,
               case_sensitive: bool = False, sort: str = "name", limit: int = 200) -> str:
    start = check_path(context.level, context.working_directory, path, "read")
    patterns = check_patterns(patterns)
    depth = check_depth(max_depth) if recursive else 1
    kind = TYPES.get(str(type).strip().lower())
    if kind is None:
        raise ToolError("type must be file, folder or any")
    if sort not in SORTS:
        raise ToolError(f"sort must be one of {', '.join(SORTS)}")
    limit = check_limit(limit, "limit", MAX_LIMIT)
    low, high = parse_size(min_size, "min_size"), parse_size(max_size, "max_size")
    if low is not None and high is not None and low > high:
        raise ToolError("min_size cannot be larger than max_size")
    since = None
    if modified_within_days is not None:
        if isinstance(modified_within_days, bool) or not isinstance(modified_within_days, (int, float)) \
                or modified_within_days <= 0:
            raise ToolError("modified_within_days must be a number above 0")
        since = time.time() - float(modified_within_days) * 86400

    stats, found, total = WalkStats(), [], 0
    for entry in walk(start, max_depth=depth, stats=stats):
        if kind != "any" and entry.kind != kind:
            continue
        if not name_matches(entry.name, patterns, case_sensitive):
            continue
        if (low is not None or high is not None) and entry.kind != "file":
            continue
        if low is not None and entry.size < low or high is not None and entry.size > high:
            continue
        if since is not None and entry.mtime < since:
            continue
        total += 1
        if len(found) < MAX_COLLECT:
            found.append((entry.relative, entry.kind, entry.size, entry.mtime))
    if sort == "name":
        found.sort(key=lambda r: (r[0].casefold(), r[0]))
    elif sort == "size":
        found.sort(key=lambda r: (-r[2], r[0]))
    else:
        found.sort(key=lambda r: (-r[3], r[0]))

    what = {"file": "files", "dir": "folders", "any": "entries"}[kind]
    shown = found[:limit]
    header = f"{start}: {total} matching {what}"
    header += ", all shown" if total == len(shown) else f", {len(shown)} shown"
    header += f", sorted by {sort}" + (" (largest first)" if sort == "size" else
                                         " (newest first)" if sort == "modified" else "")
    if len(found) < total:
        header += f" (sorting covered the first {MAX_COLLECT} matches)"
    rows = []
    for rel, entry_kind, size, mtime in shown:
        if entry_kind == "file":
            rows.append(f"file  {human_size(size):>10}  {_stamp(mtime)}  {rel}")
        elif entry_kind == "dir":
            rows.append(f"dir   {'-':>10}  {_stamp(mtime)}  {rel}/")
        elif entry_kind == "link":
            rows.append(f"link  {'-':>10}  {_stamp(mtime)}  {rel} (symbolic link, not followed)")
        else:
            rows.append(f"other {'-':>10}  {_stamp(mtime)}  {rel}")
    return "\n".join([header, stats.summary(), *rows])


# ------------------------------------------------------------------ grep

class _Matcher:
    def __init__(self, text: str, regex: bool, ignore_case: bool) -> None:
        if not isinstance(text, str) or not text:
            raise ToolError("text to search for must not be empty")
        if len(text) > 500:
            raise ToolError("text to search for is longer than 500 characters")
        self.ignore_case = ignore_case
        self.pattern = None
        if regex:
            try:
                self.pattern = re.compile(text, re.IGNORECASE if ignore_case else 0)
            except re.error as exc:
                raise ToolError(f"invalid regular expression: {exc}") from None
        self.needle = text.lower() if ignore_case else text

    def __call__(self, line: str) -> bool:
        if self.pattern is not None:
            return self.pattern.search(line[:MAX_REGEX_LINE]) is not None
        return self.needle in (line.lower() if self.ignore_case else line)


def _file_problem(head: bytes, name: str) -> str | None:
    if b"\x00" in head[:8192]:
        return "binary"
    text = head.decode("utf-8", errors="replace")
    if PRIVATE_KEY.search(text):
        return "blocked"
    if name.lower().endswith(".key") and not KEYWORD_LINE.search(text):
        return "blocked"
    return None


def _search_stream(stream, matcher: _Matcher, deadline: float, room: int) -> tuple[list[tuple[int, str]], bool]:
    """Matches (line number, text) of one file, and False if the file must be hidden."""
    found, number, ended = [], 0, True
    while True:
        piece = stream.readline(LINE_PIECE)
        if not piece:
            break
        if ended:
            number += 1
        ended = piece.endswith(b"\n")
        line = piece.decode("utf-8", errors="replace").rstrip("\r\n")
        if "PRIVATE KEY-----" in line:
            return [], False
        if len(found) < room and matcher(line):
            found.append((number, line))
        if number % 2000 == 0 and time.monotonic() > deadline:
            raise TimeoutError
    return found, True


def grep_files(context: ChampionContext, text: str, path: str = ".", patterns: list[str] | None = None,
               regex: bool = False, recursive: bool = True, max_depth: int = 10,
               ignore_case: bool = False, max_matches: int = 100) -> str:
    target = check_path(context.level, context.working_directory, path, "read")
    patterns = check_patterns(patterns)
    matcher = _Matcher(text, regex, ignore_case)
    max_matches = check_limit(max_matches, "max_matches", MAX_MATCHES)
    depth = check_depth(max_depth) if recursive else 1
    large = context.config.get("pydyna.large_file_bytes")
    deadline = time.monotonic() + WALK_SECONDS
    stats = WalkStats()
    skipped = {"binary": 0, "large": 0, "blocked": 0}
    results: list[str] = []
    files_with, searched, total = 0, 0, 0

    def search_one(fd_open, rel: str, name: str, size: int) -> None:
        nonlocal files_with, searched, total
        if size >= large:
            skipped["large"] += 1
            return
        try:
            fd = fd_open()
        except OSError:
            stats.unreadable += 1
            return
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return
            problem = _file_problem(stream.read(65536), name)
            if problem:
                skipped[problem] += 1
                return
            stream.seek(0)
            searched += 1
            found, visible = _search_stream(stream, matcher, deadline, max_matches - len(results))
        if not visible:
            skipped["blocked"] += 1
            return
        if found:
            files_with += 1
            total += len(found)
            for number, line in found:
                shown = line if len(line) <= MAX_LINE_SHOWN else line[:MAX_LINE_SHOWN] + " ..."
                results.append(f"{rel}:{number}: {shown}")

    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        if os.path.isdir(target) and not os.path.islink(target):
            for entry in walk(target, max_depth=depth, stats=stats, deadline=deadline):
                if entry.kind != "file" or not name_matches(entry.name, patterns, False):
                    continue
                search_one(lambda e=entry: os.open(e.name, flags, dir_fd=e.dir_fd), entry.relative,
                           entry.name, entry.size)
                if len(results) >= max_matches:
                    stats.stopped = stats.stopped or f"max_matches ({max_matches}) reached"
                    break
        else:
            try:
                size = os.lstat(target).st_size
            except FileNotFoundError:
                raise ToolError(f"{target} does not exist") from None
            search_one(lambda: open_no_symlinks(target, flags & ~os.O_NOFOLLOW), target.name, target.name, size)
    except TimeoutError:
        stats.stopped = "time limit reached"
    if time.monotonic() > deadline and not stats.stopped:
        stats.stopped = "time limit reached"

    header = (f"{target}: {total} matching line(s) in {files_with} file(s), {searched} file(s) searched"
              f" ({'regular expression' if regex else 'plain text'}"
              f"{', ignoring case' if ignore_case else ''})")
    notes = []
    if skipped["large"]:
        notes.append(f"{skipped['large']} file(s) of {human_size(large)} or more not searched "
                     "(large-model tools come later)")
    if skipped["binary"]:
        notes.append(f"{skipped['binary']} binary file(s) skipped")
    if skipped["blocked"]:
        notes.append(f"{skipped['blocked']} file(s) skipped (private key or non-LS-DYNA .key)")
    lines = [header, stats.summary()]
    if notes:
        lines.append("; ".join(notes))
    return "\n".join(lines + results)


# ------------------------------------------------------------------ tools

@function_tool(is_enabled=requires("read"))
async def filesystem_find(ctx: RunContextWrapper[ChampionContext], path: str = ".",
                          patterns: list[str] | None = None, recursive: bool = True, max_depth: int = 10,
                          type: str = "file", min_size: str | None = None, max_size: str | None = None,
                          modified_within_days: float | None = None, case_sensitive: bool = False,
                          sort: str = "name", limit: int = 200) -> str:
    """Find files or folders by name pattern, type, size and age, in a folder and its
    sub-folders. Symbolic links are listed but never followed; blocked (secret-looking or
    system) entries are skipped and counted. Stops after about 10 seconds.

    Args:
        path: Folder to search; relative paths start at the working folder.
        patterns: File-name patterns such as ["*.k", "*.key", "*.dyn"]; empty = all names.
            Names only: no '/' or '..'.
        recursive: Search sub-folders too (default true).
        max_depth: Deepest sub-folder level to search (1 = only the folder itself, max 50).
        type: "file" (default), "folder" or "any".
        min_size: Smallest file size, for example "100M" or "1G" (binary units).
        max_size: Largest file size, for example "10M".
        modified_within_days: Only entries changed in the last N days.
        case_sensitive: Match name patterns case-sensitively (default false).
        sort: "name" (default), "size" (largest first) or "modified" (newest first).
        limit: Maximum entries to show (1-500, default 200).
    """
    arguments = {"path": path, "patterns": patterns, "recursive": recursive, "max_depth": max_depth,
                 "type": type, "min_size": min_size, "max_size": max_size,
                 "modified_within_days": modified_within_days, "case_sensitive": case_sensitive,
                 "sort": sort, "limit": limit}
    return await run_guarded(ctx, "filesystem_find", arguments,
                             lambda: find_files(ctx.context, **arguments))


@function_tool(is_enabled=requires("read"))
async def filesystem_grep(ctx: RunContextWrapper[ChampionContext], text: str, path: str = ".",
                          patterns: list[str] | None = None, regex: bool = False, recursive: bool = True,
                          max_depth: int = 10, ignore_case: bool = False, max_matches: int = 100) -> str:
    """Find text inside text files: returns file, line number and the matching line.
    Plain text by default, so LS-DYNA keywords like *MAT_024 work as typed. Skips binary
    files, files of 100 MiB or more and blocked files. Stops after about 10 seconds.

    Args:
        text: Text to look for (plain text unless regex is true).
        path: A file, or a folder to search; relative paths start at the working folder.
        patterns: Only files whose names match, for example ["*.k", "*.key"].
        regex: Treat text as a Python regular expression (default false).
        recursive: Search sub-folders too (default true).
        max_depth: Deepest sub-folder level to search (1-50, default 10).
        ignore_case: Ignore upper/lower case (default false).
        max_matches: Maximum matching lines to return (1-1000, default 100).
    """
    arguments = {"text": text, "path": path, "patterns": patterns, "regex": regex, "recursive": recursive,
                 "max_depth": max_depth, "ignore_case": ignore_case, "max_matches": max_matches}
    return await run_guarded(ctx, "filesystem_grep", arguments,
                             lambda: grep_files(ctx.context, **arguments))


SEARCH_TOOLS = [filesystem_find, filesystem_grep]
