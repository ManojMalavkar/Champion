"""Plain command-line chat (steps 1-2a). The Textual TUI follows in fast demo step 6.

``read_line`` and ``write`` are parameters so tests can drive the chat without
a terminal. One event loop runs the whole chat, because the OpenAI client must
stay on the loop it was first used on.

Answers are Markdown (the model writes it; the TUI will render the same text).
In a terminal the final answer is rendered with ``rich`` (bold, lists, code,
tables); when output goes to a file or pipe, or with ``--plain``, it is printed
as raw text. Tool lines and BLOCKED/ERROR reasons always stay plain.

Step 4a: after a turn that changed files the chat prints one line
(``Changed files this turn: N (see /changes)``); ``/changes`` lists the
session's changes and ``/undo`` / ``/undo N`` takes them back. Deletes ask
``Approve? [y/N]`` in the chat (default no); in ``champion chat -q`` nobody
can answer, so such commands are refused.

Step 4e: ``/access`` shows and changes the per-folder access list (read or
edit per folder, plus everything else); see ``access_commands.py``.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Callable, TextIO

from agents.exceptions import MaxTurnsExceeded

from champion import __version__
from champion.access_commands import chat_access
from champion.core.config import ACCESS_LEVELS
from champion.core.hooks import ToolEvent
from champion.core.runtime import ChampionRuntime
from champion.policy.levels import BUILT_IN
from champion.policy.scope import PolicyDenied
from champion.tools.changes import changes_text, turn_summary, undo
from champion.tools.guard import ToolError

HELP = """Commands:
  /help          show this help
  /level         show the access level and the levels you can choose
  /level NAME    change the access level (1.1, 1.2, 2.1, 2.2, custom); your folders are kept
  /access        show the per-folder access list (/access help for add, set, remove, save ...)
  /audit         last tool calls of this chat (from the audit log)
  /changes       files changed in this session, newest first
  /undo          undo the last tool call's changes (file_edit, files_organize)
  /undo N        undo change number N
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


def format_event(event: ToolEvent, orchestrator: str = "Champion") -> str:
    """One line per tool call; calls made inside a specialist are indented and named."""
    prefix = "  " if event.agent in (orchestrator, "") else f"      [{event.agent}] "
    if event.phase == "start":
        return f"{prefix}-> {event.tool}({event.arguments})"
    for marker in ("BLOCKED", "ERROR"):
        if event.result_preview.startswith(marker + ":"):
            return f"{prefix}<- {event.tool}: {event.result_preview[:160]}"
    return f"{prefix}<- {event.tool}: {event.result_chars} chars in {event.seconds:.2f} s"


def level_command(runtime: ChampionRuntime, argument: str) -> str:
    context = runtime.context
    if not argument:
        lines = [f"Access level {context.level.describe()}", "Levels:"]
        lines += [f"  {level.describe()}" for level in BUILT_IN.values()]
        lines.append("  custom: from policy.custom in config.yaml")
        lines.append("Folders with their own right: /access")
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


def use_markdown(plain: bool, stream: TextIO | None = None) -> bool:
    """Render Markdown only for a terminal and only when ``--plain`` is not given."""
    stream = sys.stdout if stream is None else stream
    isatty = getattr(stream, "isatty", None)
    return not plain and callable(isatty) and bool(isatty())


def markdown_printer(console=None) -> Callable[[str], None]:
    """Return a function that prints ``champion>`` and the answer rendered as Markdown."""
    from rich.console import Console
    from rich.markdown import Markdown

    console = console or Console()

    def show(text: str) -> None:
        console.print("champion>", style="bold cyan")
        console.print(Markdown(text))

    return show


AnswerPrinter = Callable[[str], None]


def make_approver(read_line: Callable[[str], str], write: Callable[[str], None]) -> Callable:
    """y/N prompt for deletes; anything but y/yes (or no answer) is no."""
    def approve(request) -> bool:
        write(request.describe())
        try:
            reply = read_line("Approve? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        approved = reply.strip().lower() in ("y", "yes")
        write("  approved" if approved else "  not approved; nothing was run")
        return approved
    return approve


def undo_command(runtime: ChampionRuntime, argument: str) -> str:
    number = None
    if argument:
        if not argument.lstrip("#").isdigit():
            return "Use /undo or /undo N (N = number from /changes)"
        number = int(argument.lstrip("#"))
    try:
        return undo(runtime.context, number)
    except (ToolError, PolicyDenied) as exc:
        return f"Undo refused: {exc}"


async def answer(runtime: ChampionRuntime, text: str, write: Callable[[str], None],
                 show_answer: AnswerPrinter | None = None) -> bool:
    """Run one turn and print it. Returns False if the turn failed.

    ``show_answer`` prints the final answer (Markdown renderer); without it the
    answer is written as plain text after ``champion>``.
    """
    audit = runtime.context.audit
    changes_before = audit.change_count() if audit is not None else 0
    try:
        turn = await runtime.ask(text, on_tool_event=lambda e: write(format_event(e)))
    except MaxTurnsExceeded:
        write(f"Stopped: the agent used all {runtime.max_turns} turns without a final answer.")
        return False
    except Exception as exc:  # keep the chat alive after API or tool errors
        write(f"Error: {describe_error(exc)}")
        return False
    if show_answer is not None:
        show_answer(turn.text)
    else:
        write(f"champion> {turn.text}")
    write(f"  [{turn.usage.total_tokens} tokens, {turn.usage.requests} model call(s)]")
    summary = turn_summary(runtime.context, changes_before)
    if summary:
        write(summary)
    return True


async def chat_loop(runtime: ChampionRuntime, read_line: Callable[[str], str] = input,
                    write: Callable[[str], None] = print,
                    show_answer: AnswerPrinter | None = None) -> None:
    specialists = ", ".join(runtime.context.specialists) or "none yet"
    write(f"Champion {__version__} - model {runtime.config.get('llm.model_id')}, "
          f"access level {runtime.context.level.describe()}, specialists: {specialists}. /help for commands.")
    for problem in getattr(runtime, "specialist_problems", []):
        write(f"Warning: {problem}")
    for warning in runtime.context.access_warnings:
        write(f"Warning: {warning}")
    runtime.context.approver = make_approver(read_line, write)

    def confirm(prompt: str) -> bool:
        try:
            return read_line(prompt).strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False
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
        elif command == "/access":
            write(chat_access(runtime.context, argument, confirm))
        elif line == "/audit":
            write(audit_command(runtime))
        elif line == "/changes":
            write(changes_text(runtime.context))
        elif command == "/undo":
            write(undo_command(runtime, argument.strip()))
        elif line == "/new":
            runtime.new_session()
            write("New conversation started.")
        elif line == "/usage":
            u = runtime.total_usage
            write(f"{u.total_tokens} tokens ({u.input_tokens} in, {u.output_tokens} out), {u.requests} model calls")
        elif line.startswith("/"):
            write(f"Unknown command {line}. {HELP}")
        else:
            await answer(runtime, line, write, show_answer)
    write("Bye.")


def run_chat(runtime: ChampionRuntime, question: str | None = None, plain: bool = False) -> int:
    """Entry point used by ``champion chat``; returns the exit code."""
    show_answer = markdown_printer() if use_markdown(plain) else None
    try:
        if question:
            return 0 if asyncio.run(answer(runtime, question, print, show_answer)) else 1
        asyncio.run(chat_loop(runtime, show_answer=show_answer))
        return 0
    except KeyboardInterrupt:
        print("\nBye.")
        return 130
