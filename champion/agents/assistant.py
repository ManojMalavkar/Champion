"""Step 1 agent: one Agent with one tool.

An ``Agent`` is configuration only: a name, instructions (the system prompt),
the tools it may call and the model. ``Runner.run`` (in ``core/runtime.py``)
does the loop: call the model, run any tool calls, send the results back, and
repeat until the model answers with text or ``max_turns`` is reached. This is
the loop written by hand in lab 3 of the course.

In step 4 this agent is replaced by the orchestrator, which gets the PyDyna and
Linux specialists as tools.
"""

from __future__ import annotations

from agents import Agent
from agents.models.interface import Model

from champion.core.context import ChampionContext
from champion.tools.session_info import session_info

INSTRUCTIONS = """You are Champion, an assistant for CAE engineers working with LS-DYNA, PyDyna and Linux.

This is an early version. Right now you have one tool, session_info, which reports
the Champion version, local date and time, working directory, access level,
configured specialists and model.

Rules:
- Call session_info whenever the user asks about the session, the time or date,
  the working folder, the access level or which agents are available. Never guess these.
- You cannot read, list, edit or run anything yet. If asked to, say that this
  is not available in this version instead of inventing a result.
- General LS-DYNA or Python questions may be answered from your own knowledge;
  say clearly that the answer is not checked against a knowledge base yet.
- Be concise."""


def build_assistant(model: Model) -> Agent[ChampionContext]:
    return Agent[ChampionContext](
        name="Champion",
        instructions=INSTRUCTIONS,
        tools=[session_info],
        model=model,
    )
