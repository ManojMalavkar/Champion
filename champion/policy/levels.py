"""Access levels chosen by the user.

A level says *where* tools may work (only the working folder, or also outside
it) and *what* they may do there (read, or read and write):

=====  ==========================================  ========  ========
Level  Meaning                                     Read      Write
=====  ==========================================  ========  ========
1.1    Working folder, read only                   working   none
1.2    Working folder, read and write              working   working
2.1    Working folder and outside, read only       outside   none
2.2    Working folder and outside, read and write  outside   outside
custom From ``policy.custom`` (+ ``extra_dirs``)   any       any
=====  ==========================================  ========  ========

"outside" includes the working folder. Some paths are blocked at every level
(see ``sensitive.py``). The level lives in the run context, so the model can
neither see nor change it; only the user can (``--level``, ``/level``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from champion.core.config import ACCESS_LEVELS, Config

SCOPE_TEXT = {"none": "nothing", "working": "the working folder", "outside": "the working folder and outside it"}


@dataclass(frozen=True)
class AccessLevel:
    name: str
    label: str
    read: str   # none | working | outside
    write: str  # none | working | outside (never wider than read)
    extra_dirs: tuple[Path, ...] = ()

    def allows(self, right: str) -> bool:
        """``right`` is "read" or "write"."""
        return getattr(self, right) != "none"

    def describe(self) -> str:
        text = f"{self.name} ({self.label}): read {SCOPE_TEXT[self.read]}, write {SCOPE_TEXT[self.write]}"
        if self.extra_dirs:
            text += "; extra folders: " + ", ".join(str(d) for d in self.extra_dirs)
        return text

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "read": self.read, "write": self.write,
                "extra_dirs": [str(d) for d in self.extra_dirs]}


BUILT_IN = {
    "1.1": AccessLevel("1.1", "working folder, read only", "working", "none"),
    "1.2": AccessLevel("1.2", "working folder, read and write", "working", "working"),
    "2.1": AccessLevel("2.1", "working folder and outside, read only", "outside", "none"),
    "2.2": AccessLevel("2.2", "working folder and outside, read and write", "outside", "outside"),
}


def get_level(name: str, config: Config) -> AccessLevel:
    """Return the level called ``name``; ``custom`` is read from the config."""
    key = str(name).strip().lower()
    if key in BUILT_IN:
        return BUILT_IN[key]
    if key == "custom":
        custom = config.get("policy.custom")
        extra = tuple(Path(os.path.realpath(d)) for d in custom.get("extra_dirs") or [])
        return AccessLevel("custom", "custom", custom["read"], custom["write"], extra)
    raise ValueError(f"unknown access level {name!r}; choose one of {', '.join(ACCESS_LEVELS)}")
