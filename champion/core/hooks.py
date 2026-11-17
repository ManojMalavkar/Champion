"""Run hooks: watch what the agent does while it runs.

``RunHooks`` methods are called by the SDK around every tool call (and agent
start/end, model calls). ``ToolEventRecorder`` records each tool call and can
report it live through ``on_event``. The chat prints these events and the
TUI (fast demo step 6) will show them.

A specialist runs as a nested run (``Agent.as_tool``), which has its own
hooks. ``SpecialistHooks`` forwards the nested tool calls to the recorder of
the current turn, found on the run context (``ChampionContext.recorder``), so
the user sees what the specialist does, not only that it was called.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from agents import RunHooks

PREVIEW_CHARS = 200


@dataclass
class ToolEvent:
    phase: str  # "start" or "end"
    agent: str
    tool: str
    arguments: str  # JSON text the model sent
    result_preview: str = ""
    result_chars: int = 0
    seconds: float = 0.0


class ToolEventRecorder(RunHooks):
    def __init__(self, on_event: Callable[[ToolEvent], None] | None = None) -> None:
        self.events: list[ToolEvent] = []  # finished calls only
        self._on_event = on_event
        self._started: dict[str, float] = {}

    @staticmethod
    def _call_info(context: Any) -> tuple[str, str]:
        # For function tools the SDK passes a ToolContext with the call id and arguments.
        call_id = getattr(context, "tool_call_id", None) or str(id(context))
        arguments = getattr(context, "tool_arguments", None) or "{}"
        return call_id, arguments

    async def on_tool_start(self, context, agent, tool) -> None:
        call_id, arguments = self._call_info(context)
        self._started[call_id] = time.monotonic()
        if self._on_event:
            self._on_event(ToolEvent("start", agent.name, tool.name, arguments))

    async def on_tool_end(self, context, agent, tool, result) -> None:
        call_id, arguments = self._call_info(context)
        seconds = time.monotonic() - self._started.pop(call_id, time.monotonic())
        text = str(result)
        event = ToolEvent("end", agent.name, tool.name, arguments,
                          result_preview=text[:PREVIEW_CHARS], result_chars=len(text), seconds=seconds)
        self.events.append(event)
        if self._on_event:
            self._on_event(event)


class SpecialistHooks(RunHooks):
    """Hooks of a specialist's nested run: pass tool calls on to the turn's recorder."""

    @staticmethod
    def _recorder(context: Any) -> ToolEventRecorder | None:
        recorder = getattr(getattr(context, "context", None), "recorder", None)
        return recorder if isinstance(recorder, ToolEventRecorder) else None

    async def on_tool_start(self, context, agent, tool) -> None:
        recorder = self._recorder(context)
        if recorder:
            await recorder.on_tool_start(context, agent, tool)

    async def on_tool_end(self, context, agent, tool, result) -> None:
        recorder = self._recorder(context)
        if recorder:
            await recorder.on_tool_end(context, agent, tool, result)
