"""Header rows, right panel and footer of the TUI shell (T9.1).

Each widget only shows a ``ChampionUIState`` (``state.py``); none checks
access, changes files or calls agents. ``Panel.set_text`` redraws only when the
text really changed, so the 1 s clock does not redraw the other widgets.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from champion.tui import resources
from champion.tui.state import ChampionUIState

ACCENT = "bold #12b8f5"
ACCESS_COLORS = {"none": "grey50", "read": "green", "edit": "dark_orange"}
STATE_MARKS = {"idle": ("○", "grey50"), "thinking": ("●", "cyan"), "running": ("●", "green"),
               "waiting": ("●", "yellow"), "completed": ("●", "grey70"), "error": ("●", "red")}
LEVEL_COLORS = {"ok": "green", "warning": "yellow", "critical": "red", "unknown": "grey50"}


class Panel(Static):
    """Static that remembers its text and skips redraws when nothing changed."""

    def __init__(self, text: Text | str = "", **kwargs) -> None:
        super().__init__(text, **kwargs)
        self.updates = 0
        self.plain = text.plain if isinstance(text, Text) else str(text)
        self._key: Any = (self.plain, ()) if isinstance(text, str) else None

    def set_text(self, text: Text | str) -> None:
        text = Text(text) if isinstance(text, str) else text
        key = (text.plain, tuple((s.start, s.end, str(s.style)) for s in text.spans))
        if key == self._key:
            return
        self._key, self.plain = key, text.plain
        self.updates += 1
        self.update(text)


def heading(title: str) -> Text:
    return Text(title, style=ACCENT)


class SourceRow(Panel):
    """Working or Base row in the header. Choosing a JobID or location comes in T9.8 (CR-T9-4)."""

    def __init__(self, role: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.role = role

    def on_mount(self) -> None:
        self.show(None)

    def show(self, context: Any) -> None:
        text = Text(f"{self.role.capitalize():<8}│ ", style="bold")
        if context is None:
            text.append("not selected", style="grey50")
            text.append("   (JobID or location: T9.8)", style="grey35")
        else:
            source = getattr(context, "source_type", "")
            job = getattr(context, "job_id", None)
            text.append(f"{'JobID ' + str(job) if job else source:<14}│ {getattr(context, 'resolved_path', '')}  ")
            access = getattr(context, "access", "none")
            text.append(f"[{access}]", style=ACCESS_COLORS.get(access, "grey50"))
        self.set_text(text)


class AgentUsage(Panel):
    def show(self, state: ChampionUIState) -> None:
        text = heading("LIVE AGENT USAGE")
        for row in state.agents:
            mark, color = STATE_MARKS.get(row.state, STATE_MARKS["idle"])
            indent = "  " * max(row.depth - 1, 0)
            text.append(f"\n{indent}{mark} ", style=color)
            text.append(row.name)
            text.append(f"\n{indent}  {row.activity or row.state}", style="grey50")
        self.set_text(text)


class AgentSoftware(Panel):
    """Software started by agents only (process registry, CR-T9-7); nothing else is shown."""

    def show(self, state: ChampionUIState) -> None:
        text = heading("AGENT SOFTWARE")
        if not state.agent_processes:
            text.append("\nnone started by agents", style="grey50")
        for process in state.agent_processes:
            text.append("\n● ", style="green")
            text.append(f"{getattr(process, 'label', process)}  PID {getattr(process, 'pid', '?')}")
        self.set_text(text)


class ResourceUsage(Panel):
    def __init__(self, disk_target: Callable[[], tuple[Path, str]], *, meminfo: Path = resources.MEMINFO,
                 thresholds: dict | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._disk_target = disk_target
        self._meminfo = meminfo
        self._thresholds = thresholds or resources.DEFAULT_THRESHOLDS

    def on_mount(self) -> None:
        self.refresh_usage()
        self.set_interval(5.0, self.refresh_usage)

    def refresh_usage(self) -> None:
        ram = resources.memory_usage(self._meminfo)
        path, label = self._disk_target()
        disk = resources.disk_usage(path)
        text = Text()
        color = LEVEL_COLORS[resources.ram_level(ram, self._thresholds)]
        text.append(f"RAM   {resources.human(ram.used)} / {resources.human(ram.total)}  {ram.percent:.0f}%"
                    if ram else "RAM   n/a", style=color)
        color = LEVEL_COLORS[resources.disk_level(disk, self._thresholds)]
        text.append(f"\nDisk  {resources.human(disk.free)} free ({100 - disk.percent:.0f}%)"
                    if disk else "\nDisk  n/a", style=color)
        text.append(f"\n      {label}", style="grey50")
        self.set_text(text)


class ContextSummary(Panel):
    def show(self, state: ChampionUIState) -> None:
        text = heading("CONTEXT")
        for label, value in (("Files affected", state.files_affected), ("Read paths", state.read_path_count),
                             ("Edit paths", state.edit_path_count)):
            text.append(f"\n{label:<16}{value:>4}")
        self.set_text(text)


class StatusFooter(Horizontal):
    """LLM | Total Tokens | Current Time; the clock part alone is redrawn every second."""

    def __init__(self, clock: Callable[[], datetime] = datetime.now, **kwargs) -> None:
        super().__init__(**kwargs)
        self._clock = clock

    def compose(self) -> ComposeResult:
        yield Panel(id="llm")
        yield Static("│", classes="sep")
        yield Panel(id="tokens")
        yield Static("│", classes="sep")
        yield Panel(id="clock")

    def on_mount(self) -> None:
        self.tick()
        self.set_interval(1.0, self.tick)

    def show(self, state: ChampionUIState) -> None:
        self.query_one("#llm", Panel).set_text(f"LLM: {state.llm_model}")
        self.query_one("#tokens", Panel).set_text(f"Total Tokens: {state.total_tokens:,}")

    def tick(self) -> None:
        self.query_one("#clock", Panel).set_text(f"Current Time: {self._clock():%H:%M:%S}")
