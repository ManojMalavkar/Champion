"""Tool guard: the common path every Champion tool runs through.

``run_guarded`` wraps the real work of a tool:

1. look up the tool's policy entry (no entry -> blocked: deny by default);
2. check the access level allows the right the tool needs;
3. run the work in a thread (file access must not block the event loop);
4. turn ``PolicyDenied`` into ``BLOCKED: <reason>`` and ``ToolError`` (and an
   unexpected ``OSError``) into ``ERROR: <reason>`` so the model gets a clear
   message instead of a crash;
5. cap the text to ``execution.max_output_chars``;
6. write one audit row.

``run_guarded_async`` is the same path for work that is itself async, such as
a specialist agent called as a tool (step 3).

``requires(right)`` is used as ``is_enabled`` of a ``function_tool``: a tool
that needs a right the current level does not give is not even offered to
the model. The check inside ``run_guarded`` still runs, because ``is_enabled``
is only about what the model sees, not a security boundary.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from agents import RunContextWrapper

from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied
from champion.policy.tool_policy import rule_for


class ToolError(Exception):
    """The tool could not do its work (bad argument, missing file ...). Safe to show."""


def requires(right: str) -> Callable[[RunContextWrapper[ChampionContext], Any], bool]:
    def is_enabled(ctx: RunContextWrapper[ChampionContext], agent: Any) -> bool:
        return ctx.context.level.allows(right)
    return is_enabled


def cap_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    note = f"\n[output cut at {limit} of {len(text)} characters]"
    return text[: max(0, limit - len(note))] + note


async def run_guarded(ctx: RunContextWrapper[ChampionContext], tool: str, arguments: dict,
                      action: Callable[[], str]) -> str:
    """Guard a blocking action; it runs in a thread so the event loop stays free."""
    return await run_guarded_async(ctx, tool, arguments, lambda: asyncio.to_thread(action))


async def run_guarded_async(ctx: RunContextWrapper[ChampionContext], tool: str, arguments: dict,
                            action: Callable[[], Awaitable[Any]]) -> str:
    context = ctx.context
    started = time.monotonic()
    decision, reason = "allow", ""
    try:
        rule = rule_for(tool)
        if rule.right in ("read", "write") and not context.level.allows(rule.right):
            raise PolicyDenied(f"access level {context.level.name} ({context.level.label}) "
                               f"does not allow {rule.right} tools")
        text = str(await action())
        if text.startswith("ERROR:"):  # the work reported its own error (e.g. a failed specialist)
            decision, reason = "error", text[len("ERROR:"):].strip()[:200]
    except PolicyDenied as exc:
        decision, reason = "block", str(exc)
        text = f"BLOCKED: {exc}"
    except ToolError as exc:
        decision, reason = "error", str(exc)
        text = f"ERROR: {exc}"
    except OSError as exc:  # disk full, permission denied ... (step 4a write tools)
        reason = exc.strerror or exc.__class__.__name__
        decision, text = "error", f"ERROR: {reason}"
    text = cap_output(text, context.config.get("execution.max_output_chars"))
    if context.audit is not None:
        agent = getattr(getattr(ctx, "agent", None), "name", "") or ""
        context.audit.record(tool=tool, arguments=arguments, access_level=context.level.name,
                             decision=decision, reason=reason, result_chars=len(text),
                             seconds=time.monotonic() - started, agent=agent)
    return text
