"""Conversation tab (T9.3): your text on the right, answers on the left, no speaker labels.

A turn goes through ``champion.chat.answer``, so error texts, the max-turns
stop, the tokens line and the change summary are the chat's own. The TUI only
places them. Approvals: until T9.4 the TUI sets no approver, so a command that
needs approval is refused by the policy ("not possible here") and never runs.
"""

from __future__ import annotations

from typing import Any, Callable

from rich.text import Text
from textual import events
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Markdown, Static, TextArea

from champion.core.hooks import ToolEvent
from champion.tui.state import ORCHESTRATOR

USER_COLOR = "#12b8f5"
COMMANDS = ("/help", "/level", "/access", "/audit", "/changes", "/undo", "/new", "/usage")
EXIT_COMMANDS = ("/exit", "/quit")
HINT = ("Enter send · Shift+Enter or Ctrl+J new line · Up/Down history · Ctrl+C cancel · "
        "F6 right panel · Ctrl+Q quit")
BUSY_HINT = "working... Ctrl+C cancels this request"


def command_word(text: str) -> str | None:
    """The slash command of a one-line input (``/access`` etc.), else None. ``/path/x ...`` is a question."""
    if "\n" in text.strip():
        return None
    word = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return word if word in COMMANDS + EXIT_COMMANDS else None


class TeeRuntime:
    """The runtime as ``chat.answer`` sees it, but every tool event also goes to ``on_event`` first."""

    def __init__(self, runtime: Any, on_event: Callable[[ToolEvent], None]) -> None:
        self._runtime = runtime
        self._on_event = on_event

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)

    async def ask(self, text: str, on_tool_event: Callable[[ToolEvent], None] | None = None):
        def both(event: ToolEvent) -> None:
            self._on_event(event)
            if on_tool_event is not None:
                on_tool_event(event)
        return await self._runtime.ask(text, on_tool_event=both)


class ChatInput(TextArea):
    """Multi-line input: Enter sends, Shift+Enter or Ctrl+J adds a line, a paste stays one message,
    Up/Down recall earlier inputs while the box is empty or shows a recalled input."""

    NEWLINE_KEYS = ("shift+enter", "ctrl+j")

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs) -> None:
        super().__init__(soft_wrap=True, show_line_numbers=False, tab_behavior="focus", **kwargs)
        self.sent: list[str] = []  # earlier inputs (TextArea.history is the undo history)
        self._index: int | None = None

    def _browsing(self) -> bool:
        if not self.text:
            return True
        return self._index is not None and self.text == self.sent[self._index]

    def _recall(self, step: int) -> None:
        if self._index is None:
            index = len(self.sent) - 1 if step < 0 else None
        else:
            index = self._index + step
            if index >= len(self.sent):
                index = None
            index = max(index, 0) if index is not None else None
        self._index = index
        self.text = "" if index is None else self.sent[index]
        self.move_cursor(self.document.end)

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            if self.text.strip():
                text = self.text
                self.sent.append(text)
                self._index = None
                self.clear()
                self.post_message(self.Submitted(text))
            return
        if event.key in self.NEWLINE_KEYS:
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key in ("up", "down") and self.sent and self._browsing():
            event.prevent_default()
            event.stop()
            self._recall(-1 if event.key == "up" else 1)
            return
        await super()._on_key(event)


class UserMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(Text(text, style=f"bold {USER_COLOR}"), classes="user")
        self.message = text


class AnswerMessage(Markdown):
    def __init__(self, text: str) -> None:
        super().__init__(text, classes="answer")
        self.message = text


class NoteLine(Static):
    def __init__(self, text: str, kind: str = "note") -> None:
        style = {"error": "bold red", "stopped": "yellow"}.get(kind, "grey62")
        super().__init__(Text(text, style=style), classes=f"turn-note {kind}")
        self.message = text
        self.kind = kind


class ActionBlock(Static):
    """Live tool lines of one request (collapsing comes in T9.4)."""

    def __init__(self) -> None:
        super().__init__(classes="action")
        self.lines: list[str] = []
        self.agents: list[str] = []
        self.header = "▾ Working..."
        self.header_style = "cyan"
        self._draw()

    def _draw(self) -> None:
        text = Text(self.header, style=self.header_style)
        for line in self.lines:
            style = "red" if ("BLOCKED:" in line or "ERROR:" in line) else "grey62"
            text.append("\n  " + line, style=style)
        self.update(text)

    def add(self, line: str, event: ToolEvent) -> None:
        self.lines.append(line)
        if event.agent != ORCHESTRATOR and event.agent not in self.agents:
            self.agents.append(event.agent)
        self._draw()

    @property
    def tool_calls(self) -> int:
        return sum(1 for line in self.lines if "-> " in line)

    def finish(self, outcome: str, seconds: float) -> None:
        calls = self.tool_calls
        parts = [{"ok": "✓ Completed", "error": "✗ Failed", "stopped": "■ Stopped"}[outcome]]
        if self.agents:
            parts.append(" + ".join(self.agents))
        parts += [f"{calls} tool call{'s' if calls != 1 else ''}", f"{seconds:.1f} s"]
        self.header = " · ".join(parts)
        self.header_style = {"ok": "green", "error": "bold red", "stopped": "yellow"}[outcome]
        self._draw()


class ConversationView(VerticalScroll):
    """The message list. All methods run on the UI thread. Not focusable: the focus stays in the
    input (a focused list here would pull the tabs back to Conversation); scroll with the mouse."""

    can_focus = False

    def _add(self, widget) -> Any:
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def add_user(self, text: str) -> UserMessage:
        return self._add(UserMessage(text))

    def add_answer(self, text: str) -> AnswerMessage:
        return self._add(AnswerMessage(text))

    def add_note(self, text: str, kind: str = "note") -> NoteLine:
        return self._add(NoteLine(text, kind))

    def start_actions(self) -> ActionBlock:
        return self._add(ActionBlock())

    def messages(self) -> list[Any]:
        return [w for w in self.children if isinstance(w, (UserMessage, AnswerMessage, NoteLine, ActionBlock))]
