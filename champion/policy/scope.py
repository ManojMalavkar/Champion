"""Path checks: is this path allowed for reading or writing at this level?

``check_path`` is called by every file tool *before* it touches the disk, and
``open_no_symlinks`` is how the tool then opens the path. Order of checks:

1. Make the path absolute (relative paths start at the working folder) and
   normalise ``..`` lexically.
2. Blocked at every level: system folders, secret-looking names, and for
   writing operating-system folders (``sensitive.py``).
3. No symbolic links anywhere in the path. A link could point from the
   working folder to anywhere, so links are refused, not followed.
4. The level must allow the right (read / write) and the place: inside the
   working folder (or a custom extra folder) or, for levels 2.x, outside.

``open_no_symlinks`` opens the path one component at a time with
``O_NOFOLLOW``, so a link created between the check and the open is still
refused (no time-of-check / time-of-use gap).

The model's arguments are untrusted input; the reasons returned here are
shown to the model so it can explain the block to the user.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from champion.policy.levels import AccessLevel
from champion.policy.sensitive import sensitive_reason

VERB = {"read": "reading", "write": "writing"}


class PolicyDenied(Exception):
    """The access level or a fixed rule does not allow this. The message is safe to show."""


def normalise(path_text: str, working_directory: Path) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise PolicyDenied("a non-empty path is required")
    if "\x00" in path_text:
        raise PolicyDenied("the path contains a NUL character")
    path = Path(os.path.expanduser(path_text.strip()))
    if not path.is_absolute():
        path = working_directory / path
    return Path(os.path.normpath(path))


def find_symlink(path: Path) -> Path | None:
    """First component of ``path`` that is a symbolic link, or None."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return None  # the rest does not exist yet
        if stat.S_ISLNK(info.st_mode):
            return current
    return None


def is_within(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def check_path(level: AccessLevel, working_directory: Path, path_text: str, right: str) -> Path:
    """Return the normalised path if allowed, else raise ``PolicyDenied``."""
    path = normalise(path_text, working_directory)
    reason = sensitive_reason(path, write=(right == "write"))
    if reason:
        raise PolicyDenied(f"{path}: {reason}")
    link = find_symlink(path)
    if link is not None:
        raise PolicyDenied(f"{path}: symbolic links are not followed ({link} is a link); use the real path")
    scope = getattr(level, right)
    if scope == "none":
        raise PolicyDenied(f"access level {level.name} ({level.label}) does not allow {VERB[right]} files")
    areas = (working_directory, *level.extra_dirs)
    if scope != "outside" and not any(is_within(path, area) for area in areas):
        raise PolicyDenied(
            f"{path} is outside the working folder {working_directory}; access level {level.name} "
            f"allows {VERB[right]} only inside it (levels 2.1 and 2.2 also allow outside)")
    return path


def open_no_symlinks(path: Path, flags: int = os.O_RDONLY) -> int:
    """Open an absolute path without following any symbolic link; returns a file descriptor."""
    parts = path.parts
    directory = os.open(path.anchor or "/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        if len(parts) == 1:  # the root folder itself
            return os.dup(directory)
        return os.open(parts[-1], flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=directory)
    finally:
        os.close(directory)
