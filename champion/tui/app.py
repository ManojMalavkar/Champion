"""Champion TUI shell (T9.1): header, six tabs, right panel and footer.

Layout follows docs/tracks/tui.md. The shell shows what the core already has
(model, tokens, access counts, changes, agents from the registry) and says
"not available yet" where the core piece is not built. No mock data. Questions
and answers come in T9.3; the Thinking/Working logo states in T9.2.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import TabbedContent, TabPane

from champion.tui.logo import LOGO_PNG, LogoState
from champion.tui.state import ChampionUIState, build_state
from champion.tui.widgets import (AgentSoftware, AgentUsage, ContextSummary, Panel, ResourceUsage, SourceRow,
                                  StatusFooter)

TITLE = "mChampion"
TABS = (("conversation", "Conversation"), ("settings", "Settings"), ("paths", "Paths & Access"),
        ("files", "Modified Files"), ("history", "Session History"), ("hpc", "HPC Jobs"))
MIN_WIDTH, MIN_HEIGHT = 90, 24  # below this: the minimum-size message instead of the layout
NARROW_WIDTH = 110  # below this the right panel starts hidden (F6 shows it)
REFRESH_SECONDS = 2.0


def tab_note(tab: str, runtime: Any) -> str:
    notes = {
        "conversation": "Conversation comes in T9.3: your questions on the right, answers on the left, "
                        "live agent actions that collapse when done.",
        "settings": "Not available yet: needs the settings keys and session overrides (CR-T9-8, T9.12).",
        "paths": "Choosing None / Read / Edit per path comes in T9.6.\n\nCurrent access: "
                 + runtime.context.level.describe(),
        "files": "The Modified Files table (Time | Agent | Status | File) comes in T9.7.",
        "history": "Not available yet: needs the sessions table (CR-T9-6, T9.10).",
        "hpc": "Not available yet: needs the HPC agent (track T2, T9.9).",
    }
    return notes[tab]


class ChampionApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = TITLE
    BINDINGS = [Binding("f6", "toggle_right", "Right panel"), Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, runtime: Any, *, clock: Callable[[], datetime] = datetime.now,
                 logo_path: Path | None = LOGO_PNG) -> None:
        super().__init__()
        self.runtime = runtime
        self.ui_state: ChampionUIState = build_state(runtime)
        self._clock = clock
        self._logo_path = logo_path
        self._right_choice: bool | None = None  # set by F6; None = follow the terminal width

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            with Horizontal(id="header"):
                yield LogoState(self._logo_path, id="logo")
                with Vertical(id="header-text"):
                    yield Panel(TITLE, id="title")
                    yield SourceRow("working", id="working-row")
                    yield SourceRow("base", id="base-row")
            with Horizontal(id="body"):
                with TabbedContent(id="tabs", initial="conversation"):
                    for tab, label in TABS:
                        with TabPane(label, id=tab):
                            yield Panel(tab_note(tab, self.runtime), id=f"{tab}-note", classes="note")
                with Vertical(id="right"):
                    yield AgentUsage(id="agents")
                    yield AgentSoftware(id="software")
                    yield ResourceUsage(self.disk_target, id="resources")
                    yield ContextSummary(id="context")
            yield StatusFooter(self._clock, id="footer")
        yield Panel(id="too-small")

    def on_mount(self) -> None:
        self.refresh_state()
        self.apply_size(self.size.width, self.size.height)
        self.set_interval(REFRESH_SECONDS, self.refresh_state)

    def on_resize(self, event: events.Resize) -> None:
        self.apply_size(event.size.width, event.size.height)

    # ------------------------------------------------------------ state
    def disk_target(self) -> tuple[Path, str]:
        working = self.ui_state.working_context
        if working is not None and getattr(working, "resolved_path", None):
            return Path(working.resolved_path), "Working path"
        return self.runtime.context.working_directory, "Champion working folder"

    def refresh_state(self) -> None:
        """Read the core again; widgets whose text did not change are not redrawn."""
        self.ui_state = build_state(self.runtime, status=self.ui_state.runtime_status, previous=self.ui_state)
        state = self.ui_state
        self.query_one(LogoState).state = state.runtime_status
        self.query_one("#working-row", SourceRow).show(state.working_context)
        self.query_one("#base-row", SourceRow).show(state.base_context)
        self.query_one(AgentUsage).show(state)
        self.query_one(AgentSoftware).show(state)
        self.query_one(ContextSummary).show(state)
        self.query_one(StatusFooter).show(state)
        self.query_one("#paths-note", Panel).set_text(tab_note("paths", self.runtime))

    # ------------------------------------------------------------ layout
    def apply_size(self, width: int, height: int) -> None:
        small = width < MIN_WIDTH or height < MIN_HEIGHT
        self.query_one("#main").display = not small
        message = self.query_one("#too-small", Panel)
        message.display = small
        if small:
            message.set_text(Text(f"Terminal too small: {width}x{height}. "
                                  f"mChampion needs at least {MIN_WIDTH}x{MIN_HEIGHT}."))
        right = self.query_one("#right")
        right.display = self._right_choice if self._right_choice is not None else width >= NARROW_WIDTH

    def action_toggle_right(self) -> None:
        right = self.query_one("#right")
        self._right_choice = not right.display
        right.display = self._right_choice
