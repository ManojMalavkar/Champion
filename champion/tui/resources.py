"""Host RAM and disk for the right panel (T9.1).

The TUI reads these itself; it is not an agent tool, so the agents' block on
``/proc`` does not apply. Only ``MemTotal`` and ``MemAvailable`` are read from
``/proc/meminfo``. Disk is the filesystem holding the Working path, else the
Champion working folder (labelled). Thresholds become settings in T9.12.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

MEMINFO = Path("/proc/meminfo")
DEFAULT_THRESHOLDS = {"ram_warning": 85.0, "ram_critical": 95.0,
                      "disk_free_warning": 10.0, "disk_free_critical": 5.0}
GIB = 1024 ** 3


@dataclass
class Usage:
    used: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 * self.used / self.total if self.total else 0.0

    @property
    def free(self) -> int:
        return self.total - self.used


def memory_usage(meminfo: Path = MEMINFO) -> Usage | None:
    values: dict[str, int] = {}
    try:
        with open(meminfo, encoding="ascii", errors="replace") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return Usage(values["MemTotal"] - values["MemAvailable"], values["MemTotal"])


def disk_usage(path: Path) -> Usage | None:
    try:
        total, _used, free = shutil.disk_usage(path)
    except OSError:
        return None
    return Usage(total - free, total)


def ram_level(usage: Usage | None, thresholds: dict = DEFAULT_THRESHOLDS) -> str:
    if usage is None:
        return "unknown"
    if usage.percent > thresholds["ram_critical"]:
        return "critical"
    if usage.percent > thresholds["ram_warning"]:
        return "warning"
    return "ok"


def disk_level(usage: Usage | None, thresholds: dict = DEFAULT_THRESHOLDS) -> str:
    if usage is None:
        return "unknown"
    free_percent = 100.0 - usage.percent
    if free_percent < thresholds["disk_free_critical"]:
        return "critical"
    if free_percent < thresholds["disk_free_warning"]:
        return "warning"
    return "ok"


def human(size: int) -> str:
    gib = size / GIB
    return f"{gib / 1024:.1f} TB" if gib >= 1024 else f"{gib:.1f} GB"
