"""Plain command-line chat (steps 1-2). Replaced by the Textual TUI in step 9.

``read_line`` and ``write`` are parameters so tests can drive the chat without
a terminal. One event loop runs the whole chat, because the OpenAI client must
stay on the loop it was first used on.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from agents.exceptions import MaxTurnsExceeded

from champion import __version__
from champion.core.config import ACCESS_LEVELS
from champion.core.hooks import ToolEvent
from champion.core.runtime import ChampionRuntime
from champion.policy.levels import BUILT_IN

HELP = """Commands:
  /help          show this help
  /level         show the access level and the levels you can choose
  /level NAME    change the access level (1.1, 1.2, 2.1, 2.2, custom)
  /audit         last tool calls of this chat (from the audit log)
  /new           start a new conversation (forget history)
  /usage         tokens used in this chat
  /exit          quit (also /quit or Ctrl-D)"""


def describe_error(exc: Exception) -> str:
    """Short error text. For API errors only type and HTTP status are shown."""
    try:
        import openai

        if isinstance(exc, openai.APIStatusError):
            return f"{type(exc).__name__} (HTTP {exc.status_code}) from the OpenAI API"
        if isinstance(exc, openai.APIConnectionError):
            return "cannot reach the OpenAI API (connection error)"
    except ImportError:
        pass
    return f"{type(exc).__name__}: {exc}"


def format_event(event: ToolEvent) -> str:
    if event.phase == "start":
        return f"  -> {event.tool}({event.arguments})"
    for marker in ("BLOCKED", "ERROR"):
        if event.result_preview.startswith(marker + ":"):
            return f"  <- {event.tool}: {event.result_preview[:160]}"
    return f"  <- {event.tool}: {event.result_chars} chars in {event.seconds:.2f} s"


def level_command(runtime: ChampionRuntime, argument: str) -> str:
    context = runtime.context
    if not argument:
        lines = [f"Access level {context.level.describe()}", "Levels:"]
        lines += [f"  {level.describe()}" for level in BUILT_IN.values()]
        lines.append("  custom: from policy.custom in config.yaml")
        return "\n".join(lines)
    if argument.lower() not in ACCESS_LEVELS:
        return f"Unknown level {argument}; choose one of {', '.join(ACCESS_LEVELS)}"
    level = context.set_level(argument)
    return f"Access level is now {level.describe()}"


def audit_command(runtime: ChampionRuntime, limit: int = 10) -> str:
    rows = runtime.audit.recent(limit)
    if not rows:
        return "No tool calls in this chat yet."
    return "\n".join(f"  {r['ts'][11:19]} {r['access_level']:>6} {r['decision']:<5} {r['tool']} {r['arguments']}"
                     + (f" - {r['reason']}" if r["reason"] else "") for r in rows)


async def answer(runtime: ChampionRuntime, text: str, write: Callable[[str], None]) -> bool:
    """Run one turn and print it. Returns False if the turn failed."""
    try:
        turn = await runtime.ask(text, on_tool_event=lambda e: write(format_event(e)))
    except MaxTurnsExceeded:
        write(f"Stopped: the agent used all {runtime.max_turns} turns without a final answer.")
        return False
    except Exception as exc:  # keep the chat alive after API or tool errors
        write(f"Error: {describe_error(exc)}")
        return False
    write(f"champion> {turn.text}")
    write(f"  [{turn.usage.total_tokens} tokens, {turn.usage.requests} model call(s)]")
    return True


async def chat_loop(runtime: ChampionRuntime, read_line: Callable[[str], str] = input,
                    write: Callable[[str], None] = print) -> None:
    write(f"Champion {__version__} - model {runtime.config.get('llm.model_id')}, "
          f"access level {runtime.context.level.describe()}. /help for commands.")
    while True:
        try:
            line = (await asyncio.to_thread(read_line, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        command, _, argument = line.partition(" ")
        if line == "/help":
            write(HELP)
        elif command == "/level":
            write(level_command(runtime, argument.strip()))
        elif line == "/audit":
            write(audit_command(runtime))
        elif line == "/new":
            runtime.new_session()
            write("New conversation started.")
        elif line == "/usage":
            u = runtime.total_usage
            write(f"{u.total_tokens} tokens ({u.input_tokens} in, {u.output_tokens} out), {u.requests} model calls")
        elif line.startswith("/"):
            write(f"Unknown command {line}. {HELP}")
        else:
            await answer(runtime, line, write)
    write("Bye.")


def run_chat(runtime: ChampionRuntime, question: str | None = None) -> int:
    """Entry point used by ``champion chat``; returns the exit code."""
    try:
        if question:
            return 0 if asyncio.run(answer(runtime, question, print)) else 1
        asyncio.run(chat_loop(runtime))
        return 0
    except KeyboardInterrupt:
        print("\nBye.")
        return 130
