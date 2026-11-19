"""Path checks: is this path allowed for reading or editing with this access?

``check_path`` is called by every file tool *before* it touches the disk, and
``open_no_symlinks`` is how the tool then opens the path. Order of checks:

1. Make the path absolute (relative paths start at the working folder) and
   normalise ``..`` lexically. A leading ``~`` is the user's home folder at
   its real path (step 4e.8): on many sites ``/home`` is itself a link, and
   the home folder is set by the system, not by the model. Every other link
   is still refused.
2. Blocked at every level: system folders, secret-looking names, and for
   writing operating-system folders (``sensitive.py``).
3. No symbolic links anywhere in the path. A link could point from the
   working folder to anywhere, so links are refused, not followed.
4. The access list must give the right (read / edit) for this place: the
   most specific entry (working folder, one of the user's folders, or
   everything else) wins (step 4e, ``access.py``).

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

from champion.policy.access import EVERYTHING_ELSE, RANK, AccessLevel, norm_right
from champion.policy.access import is_within as is_within  # re-exported (step 2 name)
from champion.policy.sensitive import sensitive_reason

VERB = {"read": "reading", "edit": "editing"}
NOT_ENOUGH = {"none": "blocked", "read": "read only"}


class PolicyDenied(Exception):
    """The access level or a fixed rule does not allow this. The message is safe to show."""


def real_home() -> str:
    """The user's home folder with links resolved (``/home/me`` -> ``/d/people/me``)."""
    return os.path.realpath(os.path.expanduser("~"))


def expand_home(text: str) -> str:
    """``~`` and ``~/...`` -> the real home path; anything else unchanged."""
    if text == "~" or text.startswith("~/"):
        return real_home() + text[1:]
    return text


def normalise(path_text: str, working_directory: Path) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise PolicyDenied("a non-empty path is required")
    if "\x00" in path_text:
        raise PolicyDenied("the path contains a NUL character")
    path = Path(os.path.expanduser(expand_home(path_text.strip())))
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




def check_path(level: AccessLevel, working_directory: Path, path_text: str, right: str) -> Path:
    """Return the normalised path if allowed, else raise ``PolicyDenied``."""
    right = norm_right(right)  # "write" is the old name of "edit"
    if right == "none":
        raise ValueError("check_path needs the right read or edit")
    path = normalise(path_text, working_directory)
    reason = sensitive_reason(path, write=(right == "edit"))
    if reason:
        raise PolicyDenied(f"{path}: {reason}")
    link = find_symlink(path)
    if link is not None:
        raise PolicyDenied(f"{path}: symbolic links are not followed ({link} is a link); use the real path")
    if not level.allows(right):
        raise PolicyDenied(f"access level {level.name} ({level.label}) does not allow {VERB[right]} files")
    granted, where, number = level.entry_for(path, working_directory)
    if RANK[granted] >= RANK[right]:
        return path
    if where == EVERYTHING_ELSE:
        raise PolicyDenied(
            f"{path} is outside the working folder {working_directory} and the user's folders; "
            f"access {level.short()} gives everything else '{granted}', so {VERB[right]} is not allowed there "
            f"(the user can add the folder with /access add PATH {right}, "
            f"or allow it everywhere else with /access outside {right})")
    command = f"/access set {number} {right}" if number is not None else f"/access working {right}"
    raise PolicyDenied(f"{path} is {NOT_ENOUGH[granted]} ({where}, access {level.short()}); "
                       f"{VERB[right]} is not allowed there (the user can change it with {command})")


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
