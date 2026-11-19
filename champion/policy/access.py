"""Per-folder access (step 4e): read or edit per folder, chosen by the user.

Access is a list the user controls:

* the working folder: ``none``, ``read`` or ``edit``;
* any number of folders, each ``none``, ``read`` or ``edit``;
* everything else outside them: ``none``, ``read`` or ``edit``.

The most specific (longest) matching folder wins, so access can be widened
(``/data`` read, ``/data/scratch`` edit) or narrowed (working folder edit,
``reference/`` inside it read only). ``none`` blocks a folder even when
everything else is readable. ``edit`` includes ``read``; deletes still need
``edit`` plus the user's approval every time (step 4a).

The old access levels are presets of this list (1.1, 1.2, 2.1, 2.2). The old
``custom`` level (``policy.custom``: read/write scopes and ``extra_dirs``) is
converted. ``write`` is accepted as the old name of ``edit``.

Fixed blocks (system folders, secrets, ``.champion``, links, OS folders never
edited) are checked in ``scope.check_path`` before this list and always win.
Only the user changes access (``--level``, ``/level``, ``/access``, ``champion
access``); no agent tool can. Objects here are immutable: every change returns
a new ``AccessLevel``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from champion.policy.sensitive import sensitive_reason

RIGHTS = ("none", "read", "edit")
RANK = {"none": 0, "read": 1, "edit": 2}
ALIASES = {"write": "edit"}
EVERYTHING_ELSE = "everything else"

# name -> (label, working folder right, everything-else right)
PRESETS: dict[str, tuple[str, str, str]] = {
    "1.1": ("working folder, read only", "read", "none"),
    "1.2": ("working folder, read and edit", "edit", "none"),
    "2.1": ("working folder and outside, read only", "read", "read"),
    "2.2": ("working folder and outside, read and edit", "edit", "edit"),
}


class AccessError(ValueError):
    """An access change or entry the user asked for is not valid. Safe to show."""


def norm_right(value: Any) -> str:
    """``none`` / ``read`` / ``edit`` (``write`` accepted as the old name of ``edit``)."""
    text = str(value).strip().lower()
    text = ALIASES.get(text, text)
    if text not in RIGHTS:
        raise AccessError(f"right must be one of {', '.join(RIGHTS)} (got {value!r})")
    return text


def is_within(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


@dataclass(frozen=True)
class Folder:
    path: Path  # real path, no links
    right: str  # none | read | edit


@dataclass(frozen=True)
class AccessLevel:
    name: str  # preset name (1.1 ... 2.2) when working/outside match one, else "custom"
    label: str
    working: str
    outside: str
    folders: tuple[Folder, ...] = ()

    # ------------------------------------------------------------ queries
    @property
    def extra_dirs(self) -> tuple[Path, ...]:
        return tuple(f.path for f in self.folders)

    def rights(self) -> list[str]:
        return [self.working, self.outside, *(f.right for f in self.folders)]

    def allows(self, right: str) -> bool:
        """True if any entry gives ``right`` (``read`` or ``edit``; ``write`` = ``edit``)."""
        need = RANK[norm_right(right)]
        return any(RANK[r] >= need for r in self.rights())

    def entry_for(self, path: Path, working_directory: Path) -> tuple[str, str, int | None]:
        """``(right, where, number)`` of the most specific entry for ``path``.

        ``where`` is ``the working folder``, ``folder N`` or ``everything else``;
        ``number`` is N for a folder entry (None otherwise). A folder entry with
        the same path as the working folder overrides it.
        """
        best: tuple[Path, str, str, int | None] | None = None
        candidates = [(working_directory, self.working, "the working folder", None)]
        candidates += [(f.path, f.right, f"folder {i}", i) for i, f in enumerate(self.folders, 1)]
        for folder, right, where, number in candidates:
            if is_within(path, folder) and (best is None or len(folder.parts) >= len(best[0].parts)):
                best = (folder, right, where, number)
        if best is None:
            return self.outside, EVERYTHING_ELSE, None
        return best[1], best[2], best[3]

    def right_for(self, path: Path, working_directory: Path) -> str:
        return self.entry_for(path, working_directory)[0]

    def describe(self) -> str:
        text = (f"{self.name} ({self.label}): working folder {self.working}, "
                f"everything else {self.outside}")
        if self.folders:
            count = len(self.folders)
            text += f"; {count} folder{'s' if count != 1 else ''}: "
            text += ", ".join(f"{f.path} {f.right}" for f in self.folders)
        return text

    def short(self) -> str:
        """For the banner: ``2.1`` or ``2.1 + 2 folders``."""
        if not self.folders:
            return self.name
        count = len(self.folders)
        return f"{self.name} + {count} folder{'s' if count != 1 else ''}"

    def table(self, working_directory: Path) -> str:
        rows = [("W", f"{working_directory}   (working)", self.working)]
        rows += [(str(i), str(f.path), f.right) for i, f in enumerate(self.folders, 1)]
        rows.append(("*", EVERYTHING_ELSE, self.outside))
        width = max(len(r[1]) for r in rows)
        lines = [f"  #  {'folder':<{width}}  right"]
        lines += [f"  {n:<2} {folder:<{width}}  {right}" for n, folder, right in rows]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "working": self.working, "outside": self.outside,
                "folders": [{"path": str(f.path), "right": f.right} for f in self.folders]}

    # ------------------------------------------------------------ changes (new objects)
    def with_rights(self, *, working: str | None = None, outside: str | None = None) -> "AccessLevel":
        return make(norm_right(working) if working is not None else self.working,
                    norm_right(outside) if outside is not None else self.outside, self.folders)

    def with_preset(self, name: str) -> "AccessLevel":
        if name not in PRESETS:
            raise AccessError(f"unknown preset {name!r}; choose one of {', '.join(PRESETS)}")
        _, working, outside = PRESETS[name]
        return make(working, outside, self.folders)

    def with_folder(self, path: Path, right: str) -> "AccessLevel":
        """Add ``path`` (already checked, see ``check_folder``) or change its right."""
        right = norm_right(right)
        folders = list(self.folders)
        for i, folder in enumerate(folders):
            if folder.path == path:
                folders[i] = Folder(path, right)
                break
        else:
            folders.append(Folder(path, right))
        return make(self.working, self.outside, folders)

    def with_folder_right(self, number: int, right: str) -> "AccessLevel":
        folder = self.folder(number)
        return self.with_folder(folder.path, right)

    def without_folder(self, number: int) -> "AccessLevel":
        self.folder(number)
        folders = [f for i, f in enumerate(self.folders, 1) if i != number]
        return make(self.working, self.outside, folders)

    def folder(self, number: int) -> Folder:
        if not 1 <= number <= len(self.folders):
            raise AccessError(f"no folder {number}; /access lists the folder numbers")
        return self.folders[number - 1]


def make(working: str, outside: str, folders: Iterable[Folder] = ()) -> AccessLevel:
    """Build an ``AccessLevel``; its name is the preset with the same working/outside rights."""
    working, outside = norm_right(working), norm_right(outside)
    for name, (label, w, o) in PRESETS.items():
        if (w, o) == (working, outside):
            return AccessLevel(name, label, working, outside, tuple(folders))
    return AccessLevel("custom", "custom", working, outside, tuple(folders))


BUILT_IN = {name: make(w, o) for name, (_, w, o) in PRESETS.items()}


# ---------------------------------------------------------------- folders

def check_folder(path_text: str, working_directory: Path, right: str) -> Path:
    """Check a folder the user wants to add; return its real path or raise ``AccessError``.

    ``~`` and relative paths (from the working folder) are resolved; links in
    the parents are resolved to the real path, but the folder itself must not
    be a link. System and secret folders are refused; for ``edit`` also
    operating-system folders such as ``/etc`` and ``/usr``.
    """
    right = norm_right(right)
    if not isinstance(path_text, str) or not path_text.strip() or "\x00" in path_text:
        raise AccessError("a folder path is required")
    path = Path(os.path.expanduser(path_text.strip()))
    if not path.is_absolute():
        path = working_directory / path
    path = Path(os.path.normpath(path))
    if path.is_symlink():
        raise AccessError(f"{path} is a symbolic link; add the real folder instead")
    if not path.is_dir():
        raise AccessError(f"{path} is not an existing folder")
    real = Path(os.path.realpath(path))
    if right != "none":
        reason = sensitive_reason(real, write=(right == "edit"))
        if reason:
            raise AccessError(f"{real}: {reason}")
    return real


def is_wide(path: Path, working_directory: Path) -> bool:
    """Edit access here would reach very far: ``/``, the home folder, or a parent of the working folder."""
    home = Path(os.path.realpath(os.path.expanduser("~")))
    return path == Path("/") or path == home or (is_within(working_directory, path) and path != working_directory)


# ---------------------------------------------------------------- building from config

def from_legacy_custom(custom: dict, working_directory: Path) -> AccessLevel:
    """Convert ``policy.custom`` (read/write scopes none|working|outside, extra_dirs)."""
    read, write = custom.get("read", "working"), custom.get("write", "none")
    working = "edit" if write != "none" else ("read" if read != "none" else "none")
    outside = "edit" if write == "outside" else ("read" if read == "outside" else "none")
    folder_right = "edit" if write != "none" else ("read" if read != "none" else "none")
    folders = [Folder(Path(os.path.realpath(d)), folder_right) for d in custom.get("extra_dirs") or []]
    return make(working, outside, folders)


def preset_level(name: str, config) -> AccessLevel:
    """A preset by name; ``custom`` is the converted ``policy.custom``."""
    key = str(name).strip().lower()
    if key in BUILT_IN:
        return BUILT_IN[key]
    if key == "custom":
        work = Path(os.path.realpath(config.get("execution.working_directory")))
        return from_legacy_custom(config.get("policy.custom") or {}, work)
    raise ValueError(f"unknown access level {name!r}; choose one of {', '.join([*BUILT_IN, 'custom'])}")


def apply_entries(level: AccessLevel, data: dict, working_directory: Path,
                  warnings: list[str], source: str) -> AccessLevel:
    """Apply ``working``, ``outside`` and ``folders`` from a config or saved mapping.

    Bad folders are skipped with a warning, so one moved folder does not stop
    Champion from starting.
    """
    if data.get("working") is not None:
        level = level.with_rights(working=data["working"])
    if data.get("outside") is not None:
        level = level.with_rights(outside=data["outside"])
    for entry in data.get("folders") or []:
        try:
            right = norm_right(entry.get("right", "read"))
            level = level.with_folder(check_folder(str(entry.get("path", "")), working_directory, right), right)
        except (AccessError, AttributeError) as exc:
            warnings.append(f"{source}: folder {entry!r} skipped: {exc}")
    return level


def load_saved(path: Path | None) -> dict | None:
    """The saved access (``/access save``) or None. Refuses links and files others can write."""
    if path is None or not os.path.lexists(path):
        return None
    if os.path.islink(path):
        raise AccessError(f"{path} is a symbolic link; not used")
    if os.stat(path).st_mode & 0o022:
        raise AccessError(f"{path} can be changed by other users (chmod 600 it); not used")
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise AccessError(f"{path} must contain a mapping")
    return data


def save(level: AccessLevel, path: Path) -> None:
    """Write the access list to ``path`` (mode 600, folder 700, no links, atomic)."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.path.islink(path) or os.path.islink(path.parent):
        raise AccessError(f"{path} or its folder is a symbolic link; not saved")
    data = {"working": level.working, "outside": level.outside,
            "folders": [{"path": str(f.path), "right": f.right} for f in level.folders]}
    text = "# Champion access, saved with /access save. Edit with /access or champion access.\n"
    text += yaml.safe_dump(data, sort_keys=False)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def build_access(config, *, level: str | None = None, saved_file: Path | None = None,
                 read: Iterable[str] = (), edit: Iterable[str] = (), outside: str | None = None,
                 ) -> tuple[AccessLevel, list[str]]:
    """Starting access for a session and the warnings to show.

    Order (later wins): preset (``access.preset``, else ``policy.default_level``)
    -> ``access`` section of config.yaml -> saved file -> command line
    (``--level`` sets working/outside from that preset and keeps the folders,
    then ``--read``/``--edit`` folders and ``--outside``).
    """
    warnings: list[str] = []
    work = Path(os.path.realpath(config.get("execution.working_directory")))
    section = config.get("access") or {}
    current = preset_level(section.get("preset") or config.get("policy.default_level"), config)
    current = apply_entries(current, section, work, warnings, "config access")
    try:
        saved = load_saved(saved_file)
    except (AccessError, OSError, yaml.YAMLError) as exc:
        warnings.append(f"saved access not used: {exc}")
        saved = None
    if saved is not None:
        current = make(current.working, current.outside)  # the saved list replaces the config folders
        current = apply_entries(current, saved, work, warnings, str(saved_file))
    if level:
        current = current.with_preset(level) if level in PRESETS else preset_level(level, config)
    for right, paths in (("read", read), ("edit", edit)):
        for text in paths:
            current = current.with_folder(check_folder(text, work, right), right)
    if outside is not None:
        current = current.with_rights(outside=outside)
    return current, warnings
