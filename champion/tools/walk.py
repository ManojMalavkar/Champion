"""Safe folder walk, shared by ``filesystem_find``, ``filesystem_grep`` and
``disk_analysis`` (step 4a).

Rules (the same as the step 2 file tools):

* the start folder is checked with ``check_path`` by the caller and opened
  with ``open_no_symlinks``;
* every sub-folder is opened relative to its parent's file descriptor with
  ``O_NOFOLLOW``, so a folder that is swapped for a symbolic link during the
  walk is refused, not followed;
* symbolic links are reported (kind ``link``) but never followed;
* entries with secret-looking names, Champion's own ``.champion`` folder and
  system folders (``/proc``, ``/sys`` ...) are skipped and counted;
* the walk stops at ``max_depth``, after ``seconds`` (default 10 s) or after
  ``max_entries`` entries, and says so in ``WalkStats.stopped``.

Entries come in name order, so results are the same on every run.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from champion.policy.scope import open_no_symlinks
from champion.policy.sensitive import name_reason, sensitive_reason
from champion.tools.guard import ToolError

WALK_SECONDS = 10.0
MAX_ENTRIES = 200_000
MAX_DEPTH = 50


@dataclass
class Entry:
    path: Path       # absolute path
    relative: str    # path relative to the start folder
    name: str
    kind: str        # file | dir | link | other
    size: int
    mtime: float
    depth: int       # 1 = directly inside the start folder
    dir_fd: int      # descriptor of the parent folder; valid only while the entry is being handled
    inode: tuple[int, int]


@dataclass
class WalkStats:
    scanned: int = 0
    blocked: int = 0      # skipped: secret-looking names, .champion, system folders
    links: int = 0        # symbolic links seen (never followed)
    unreadable: int = 0   # folders that could not be opened
    stopped: str = ""     # why the walk stopped early (empty: it finished)

    def summary(self) -> str:
        parts = [f"{self.scanned} entries scanned"]
        if self.blocked:
            parts.append(f"{self.blocked} skipped (blocked: secret-looking or system)")
        if self.links:
            parts.append(f"{self.links} symbolic link(s) not followed")
        if self.unreadable:
            parts.append(f"{self.unreadable} folder(s) not readable")
        text = "; ".join(parts)
        if self.stopped:
            text += f"; STOPPED EARLY: {self.stopped} (results may be incomplete)"
        return text


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def check_depth(max_depth: int) -> int:
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= MAX_DEPTH:
        raise ToolError(f"max_depth must be between 1 and {MAX_DEPTH}")
    return max_depth


def walk(start: Path, *, max_depth: int, stats: WalkStats, deadline: float | None = None,
         max_entries: int = MAX_ENTRIES) -> Iterator[Entry]:
    """Yield the entries below ``start`` (already checked by the caller)."""
    deadline = time.monotonic() + WALK_SECONDS if deadline is None else deadline
    try:
        fd = open_no_symlinks(start, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        raise ToolError(f"{start} does not exist") from None
    except NotADirectoryError:
        raise ToolError(f"{start} is not a folder") from None
    except OSError as exc:
        raise ToolError(f"cannot open {start} ({exc.strerror})") from None
    try:
        yield from _walk_dir(fd, start, "", 1, max_depth, stats, deadline, max_entries)
    finally:
        os.close(fd)


def _walk_dir(fd: int, folder: Path, relative: str, depth: int, max_depth: int, stats: WalkStats,
              deadline: float, max_entries: int) -> Iterator[Entry]:
    try:
        with os.scandir(fd) as scan:
            entries = sorted(scan, key=lambda e: (e.name.casefold(), e.name))
    except OSError:
        stats.unreadable += 1
        return
    for item in entries:
        if stats.stopped:
            return
        if time.monotonic() > deadline:
            stats.stopped = "time limit reached"
            return
        if stats.scanned >= max_entries:
            stats.stopped = f"{max_entries} entries scanned"
            return
        stats.scanned += 1
        path = folder / item.name
        if name_reason(item.name) or sensitive_reason(path):
            stats.blocked += 1
            continue
        try:
            info = item.stat(follow_symlinks=False)
        except OSError:
            stats.unreadable += 1
            continue
        kind = _kind(info.st_mode)
        rel = f"{relative}/{item.name}" if relative else item.name
        if kind == "link":
            stats.links += 1
        yield Entry(path, rel, item.name, kind, info.st_size if kind == "file" else 0, info.st_mtime,
                    depth, fd, (info.st_dev, info.st_ino))
        if kind == "dir" and depth < max_depth:
            try:
                child = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                stats.unreadable += 1
                continue
            try:
                yield from _walk_dir(child, path, rel, depth + 1, max_depth, stats, deadline, max_entries)
            finally:
                os.close(child)
