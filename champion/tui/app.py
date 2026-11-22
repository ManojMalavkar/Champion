"""Champion TUI shell (T9.1): header, six tabs, right panel and footer.

Layout follows docs/tracks/tui.md. The shell shows what the core already has
(model, tokens, access counts, changes, agents from the registry) and says
"not available yet" where the core piece is not built. No mock data.

T9.3: the Conversation tab sends one request at a time. The turn runs in a
thread worker with its own event loop (so a blocking approver can be added in
T9.4) and goes through ``champion.chat.answer``. UI updates from the worker go
through ``call_from_thread``. Ctrl+C cancels the running request. The logo
state follows the turn: thinking (no tool open), working (a tool runs), idle,
error (the request ended with an error); the pulse comes in T9.2.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import TabbedContent, TabPane

from champion.chat import answer, format_event
from champion.core.hooks import ToolEvent
from champion.tui.conversation import (BUSY_HINT, HINT, ActionBlock, ChatInput, ConversationView, TeeRuntime,
                                       command_word)
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
    BINDINGS = [Binding("f6", "toggle_right", "Right panel", priority=True), Binding("ctrl+q", "quit", "Quit"),
                Binding("ctrl+c", "cancel_turn", "Cancel", priority=True)]

    def __init__(self, runtime: Any, *, clock: Callable[[], datetime] = datetime.now,
                 logo_path: Path | None = LOGO_PNG) -> None:
        super().__init__()
        self.runtime = runtime
        self.ui_state: ChampionUIState = build_state(runtime)
        self._clock = clock
        self._logo_path = logo_path
        self._right_choice: bool | None = None  # set by F6; None = follow the terminal width
        self.busy = False
        self._turn_loop: asyncio.AbstractEventLoop | None = None
        self._turn_task: asyncio.Task | None = None
        self._actions: ActionBlock | None = None
        self._open_tools = 0
        self._expected_tool_line: str | None = None
        self._last_event: ToolEvent | None = None

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
                            if tab == "conversation":
                                yield ConversationView(id="conversation-log")
                                yield ChatInput(id="chat-input")
                                yield Panel(HINT, id="input-hint")
                            else:
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
        self.query_one(ChatInput).focus()

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
        title = TITLE if state.runtime_status == "idle" else f"{TITLE} · {state.runtime_status}"
        self.query_one("#title", Panel).set_text(title)
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

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Back on Conversation the input gets the focus; on other tabs the hidden input lets it go."""
        chat_input = self.query_one(ChatInput)
        if event.pane.id == "conversation":
            if not chat_input.disabled:
                chat_input.focus()
        elif self.focused is chat_input:
            self.set_focus(None)

    # ------------------------------------------------------------ conversation (T9.3)
    def set_status(self, status: str) -> None:
        self.ui_state.runtime_status = status
        self.refresh_state()

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        if self.busy:
            return
        view = self.query_one(ConversationView)
        text = message.text
        word = command_word(text)
        if word in ("/exit", "/quit"):
            self.exit()
            return
        view.add_user(text)
        if word is not None:
            view.add_note(f"{word}: slash commands come later (T9.5). Use the tabs: Paths & Access, "
                          "Modified Files. Nothing was sent to the model.")
            return
        self.busy = True
        chat_input = self.query_one(ChatInput)
        chat_input.disabled = True
        self.query_one("#input-hint", Panel).set_text(BUSY_HINT)
        self._actions = view.start_actions()
        self._open_tools = 0
        self.set_status("thinking")
        self.run_turn(text, time.monotonic())

    @work(thread=True, exclusive=True, group="turn")
    def run_turn(self, text: str, started: float) -> None:
        """One request in its own event loop; the result goes back to the UI thread."""
        loop = asyncio.new_event_loop()
        outcome = "error"
        try:
            proxy = TeeRuntime(self.runtime, self._tool_event_from_worker)
            task = loop.create_task(answer(proxy, text, self._write_from_worker, self._answer_from_worker))
            self._turn_loop, self._turn_task = loop, task
            try:
                outcome = "ok" if loop.run_until_complete(task) else "error"
            except asyncio.CancelledError:
                outcome = "stopped"
        finally:
            self._turn_loop = self._turn_task = None
            loop.close()
            self.call_from_thread(self.turn_finished, outcome, time.monotonic() - started)

    def _tool_event_from_worker(self, event: ToolEvent) -> None:
        self._expected_tool_line = format_event(event)
        self.call_from_thread(self.tool_event, event)

    def _write_from_worker(self, line: str) -> None:
        if line == self._expected_tool_line:
            self._expected_tool_line = None
            self.call_from_thread(self.tool_line, line)
        else:
            self.call_from_thread(self.turn_note, line)

    def _answer_from_worker(self, text: str) -> None:
        self.call_from_thread(self.query_one(ConversationView).add_answer, text)

    def tool_event(self, event: ToolEvent) -> None:
        self._open_tools = max(0, self._open_tools + (1 if event.phase == "start" else -1))
        self._last_event = event
        self.set_status("working" if self._open_tools else "thinking")

    def tool_line(self, line: str) -> None:
        if self._actions is not None and self._last_event is not None:
            self._actions.add(line, self._last_event)

    def turn_note(self, line: str) -> None:
        kind = "error" if line.startswith("Error:") else "stopped" if line.startswith("Stopped:") else "note"
        self.query_one(ConversationView).add_note(line.strip(), kind)

    def turn_finished(self, outcome: str, seconds: float) -> None:
        view = self.query_one(ConversationView)
        if self._actions is not None:
            if self._actions.lines or outcome != "ok":
                self._actions.finish(outcome, seconds)
            else:
                self._actions.remove()  # no tool call: only the answer is shown
        if outcome == "stopped":
            view.add_note("Stopped by the user. Nothing more is run for this request.", "stopped")
        self._actions = None
        self.busy = False
        chat_input = self.query_one(ChatInput)
        chat_input.disabled = False
        chat_input.focus()
        self.query_one("#input-hint", Panel).set_text(HINT)
        self.set_status("error" if outcome == "error" else "idle")

    def action_cancel_turn(self) -> None:
        loop, task = self._turn_loop, self._turn_task
        if self.busy and loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
