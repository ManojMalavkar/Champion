"""``/memory`` commands of the chat (M6, CR-2); the TUI uses the same function.

Only the user changes memory here: preferences are stored with source
``user``; summary items change status only by a user command.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from champion.memory.preferences import SPECS
from champion.memory.storage import MemoryRefused

HELP = """Memory commands:
  /memory                     what memory is on, sizes, unfinished requests, problems
  /memory show                the memory notes the orchestrator gets now
  /memory prefs               your preferences (/memory keys: the keys you can set)
  /memory set KEY VALUE       set a preference (lists: a,b; mappings: name=value,name=value)
  /memory forget KEY          remove a preference
  /memory summaries           active items of the summaries (ID#N)
  /memory done ID#N           mark a summary item done (also: reject ID#N)
  /memory project [FOLDER|off]  keep summaries in FOLDER/.champion/memory (needs edit access)
  /memory unfinished [close]  requests that never ended; close marks them failed
  /memory clear               forget the working memory of this conversation"""

OFF = "Memory is off (memory.enabled: false in config.yaml)."


def _value(key: str, text: str) -> Any:
    spec = SPECS.get(key)
    if spec is None:
        raise MemoryRefused(f"unknown preference {key!r}; /memory keys lists them")
    text = text.strip()
    if spec.kind == "bool":
        lowered = text.lower()
        if lowered not in ("true", "false", "yes", "no", "on", "off"):
            raise MemoryRefused(f"{key} is true or false")
        return lowered in ("true", "yes", "on")
    if spec.kind == "paths":
        return [item.strip() for item in text.split(",") if item.strip()]
    if spec.kind == "map":
        pairs = [item.partition("=") for item in text.split(",") if item.strip()]
        if not pairs or any(not sep or not name.strip() for name, sep, _ in pairs):
            raise MemoryRefused(f"{key} is name=value,name=value")
        return {name.strip(): value.strip() for name, _, value in pairs}
    return text


def _status(memory: Any) -> str:
    lines = ["Memory is on."]
    lines.append(f"  history      : last {memory.settings.max_turns} turns, about {memory.settings.max_tokens} tokens")
    lines.append(f"  runtime state: {memory.runtime_store.folder if memory.runtime_store else 'off'}")
    store = memory.summaries
    if store is None:
        lines.append("  summaries    : off")
    else:
        active = len(memory._safe("summaries", store.active_items) or [])
        lines.append(f"  summaries    : {store.folder} ({active} active item(s), "
                     f"{len(memory.pending_turns())} dropped turn(s) waiting)")
    if memory.preferences is None:
        lines.append("  preferences  : off")
    else:
        prefs = (memory._safe("preferences", memory.preferences.all) or {}).get("preferences", {})
        lines.append(f"  preferences  : {memory.preferences.path} ({len(prefs)} set)")
    lines.append(f"  project      : {memory.project or 'none (user memory folder)'}")
    unfinished = memory.unfinished()
    if unfinished:
        lines.append(f"  unfinished   : {len(unfinished)} request(s); /memory unfinished")
    for problem in memory.problems[-5:]:
        lines.append(f"  problem      : {problem}")
    lines.append("/memory help for the commands")
    return "\n".join(lines)


def _summary_ref(text: str) -> tuple[str, int]:
    summary_id, sep, index = text.strip().partition("#")
    if not sep or not index.isdigit():
        raise MemoryRefused("use ID#N from /memory summaries, for example SUM-20261121-001#0")
    return summary_id, int(index)


def _project(runtime: Any, memory: Any, argument: str) -> str:
    if not argument:
        return f"Project memory folder: {memory.project or 'none (summaries in the user memory folder)'}"
    if argument.lower() == "off":
        memory.project = None
        return "Project memory off: summaries go to the user memory folder."
    from champion.policy.scope import PolicyDenied, check_path

    context = runtime.context
    try:
        folder = check_path(context.level, context.working_directory, argument, "edit")
    except PolicyDenied as exc:
        return f"Project memory refused: {exc}"
    if not folder.is_dir():
        return f"Project memory refused: {folder} is not a folder"
    memory.project = Path(os.path.realpath(folder))
    return f"Project memory folder: {memory.project} (summaries in {memory.summaries.folder if memory.summaries else '-'})"


def memory_command(runtime: Any, argument: str) -> str:
    memory = getattr(runtime.context, "memory", None)
    if memory is None:
        return OFF
    command, _, rest = argument.strip().partition(" ")
    command, rest = command.lower(), rest.strip()
    try:
        if not command:
            return _status(memory)
        if command == "help":
            return HELP
        if command == "show":
            return memory.instruction_blocks(orchestrator=True).strip() or "No memory notes yet."
        if command == "keys":
            return "\n".join(f"  {key:<32} {spec.help}" for key, spec in SPECS.items())
        if command in ("prefs", "set", "forget") and memory.preferences is None:
            return "Preferences are off (memory.preferences: false)."
        if command == "prefs":
            prefs = memory.preferences.all()["preferences"]
            return "\n".join(f"  {k}: {v}" for k, v in prefs.items()) or "No preferences set (/memory keys)."
        if command == "set":
            key, _, value = rest.partition(" ")
            if not key or not value.strip():
                return "Use /memory set KEY VALUE (/memory keys lists the keys)"
            stored = memory.preferences.set(key, _value(key, value), source="user")
            return f"Preference {key} = {stored}"
        if command == "forget":
            if not rest:
                return "Use /memory forget KEY"
            return f"Preference {rest} removed." if memory.preferences.forget(rest) else f"No preference {rest}."
        if command in ("summaries", "done", "reject") and memory.summaries is None:
            return "Summaries are off (memory.summaries: false)."
        if command == "summaries":
            items = memory.summaries.active_items()
            return "\n".join(f"  {sid}#{i} [{item.kind}] {item.text}" for sid, i, item in items) \
                or "No active summary items."
        if command in ("done", "reject"):
            summary_id, index = _summary_ref(rest)
            status = "done" if command == "done" else "rejected"
            item = memory.summaries.set_status(summary_id, index, status)
            return f"{summary_id}#{index} is now {status}: {item.text}"
        if command == "project":
            return _project(runtime, memory, rest)
        if command == "unfinished":
            if rest == "close":
                closed = memory.close_unfinished()
                return f"Marked failed: {', '.join(closed)}" if closed else "No unfinished requests."
            states = memory.unfinished()
            if not states:
                return "No unfinished requests."
            lines = [f"  {s.request_id} {s.started_at[:19]} step {s.current_step}, {len(s.tool_calls)} tool call(s)"
                     for s in states]
            return "\n".join(lines + ["/memory unfinished close marks them failed (only if no other chat runs "
                                      "in this folder)"])
        if command == "clear":
            memory.working.clear_task()
            return "Working memory of this conversation cleared."
    except MemoryRefused as exc:
        return f"Memory refused: {exc}"
    except OSError as exc:
        return f"Memory error: {exc.strerror or type(exc).__name__}"
    return f"Unknown memory command {command}. {HELP}"
