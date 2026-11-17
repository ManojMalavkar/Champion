"""Runtime: connects config, OpenAI client, agent, session and Runner.

One ``ChampionRuntime`` lives for one chat. Each ``ask()`` is one
``Runner.run``: the SDK loop of model call -> tool calls -> model call ... until
the agent answers with text or ``orchestration.max_steps`` turns are used.

Design points:
* One ``AsyncOpenAI`` client, created here with the key from
  ``get_openai_api_key``; the key is not put in ``os.environ`` or a global.
  ``llm.base_url`` can point the same client at a gateway. With
  ``llm.provider: databricks`` it is the workspace Unity AI Gateway
  (``/ai-gateway/mlflow/v1``), which speaks the Chat Completions API, so the
  model is ``OpenAIChatCompletionsModel``; with ``openai`` it is
  ``OpenAIResponsesModel``.
* Tracing is off unless ``llm.tracing`` is true (SDK traces go to OpenAI).
* Conversation memory is an in-memory ``SQLiteSession``; only the last
  ``orchestration.history_turns`` turns are sent with each request, which keeps
  cost and context size bounded. ``new_session()`` forgets everything.
* The access level (``level`` argument, else ``policy.default_level``) and
  the audit log (``storage.audit_db``) live in the run context (step 2).
"""

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from agents import (Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel, RunConfig, Runner,
                    SQLiteSession)
from agents.models.interface import Model
from openai import AsyncOpenAI

from champion.core.config import PLACEHOLDER_MODEL, Config, get_openai_api_key
from champion.core.context import ChampionContext
from champion.core.hooks import ToolEvent, ToolEventRecorder
from champion.storage.audit import AuditLog


class RuntimeSetupError(RuntimeError):
    """Champion cannot start (no key, no model). The message says what to fix."""


def build_client(config: Config, env: Mapping[str, str] | None = None) -> AsyncOpenAI:
    api_key_env = config.get("llm.api_key_env")
    key, _source = get_openai_api_key(config.root, env, api_key_env)
    if key is None:
        raise RuntimeSetupError(
            f"{api_key_env} not found in the environment or .env; run 'champion doctor'"
        )
    return AsyncOpenAI(api_key=key, base_url=config.get("llm.base_url"))


def build_model(config: Config, client: AsyncOpenAI) -> Model:
    model_id = config.get("llm.model_id")
    if model_id == PLACEHOLDER_MODEL:
        raise RuntimeSetupError("llm.model_id is not set in config.yaml; run 'champion doctor'")
    if config.get("llm.provider") == "databricks":
        return OpenAIChatCompletionsModel(model=model_id, openai_client=client)
    return OpenAIResponsesModel(model=model_id, openai_client=client)


def _is_user_message(item: Any) -> bool:
    return isinstance(item, Mapping) and item.get("role") == "user" and item.get("type", "message") == "message"


def keep_last_turns(history: list, new_input: list, max_turns: int) -> list:
    """Session input callback: keep only the last ``max_turns`` earlier turns.

    A turn starts at a user message and includes everything after it (tool
    calls, tool results, the answer), so cutting at a user message never
    separates a tool call from its result.
    """
    starts = [i for i, item in enumerate(history) if _is_user_message(item)]
    if len(starts) > max_turns:
        history = history[starts[-max_turns]:]
    return list(history) + list(new_input)


def make_run_config(config: Config) -> RunConfig:
    return RunConfig(
        tracing_disabled=not config.get("llm.tracing"),
        trace_include_sensitive_data=False,
        workflow_name="Champion",
        session_input_callback=functools.partial(
            keep_last_turns, max_turns=config.get("orchestration.history_turns")),
    )


@dataclass
class TurnUsage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "TurnUsage") -> None:
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens


@dataclass
class TurnResult:
    text: str
    tool_events: list[ToolEvent] = field(default_factory=list)
    usage: TurnUsage = field(default_factory=TurnUsage)


class ChampionRuntime:
    def __init__(self, config: Config, *, model: Model | None = None,
                 env: Mapping[str, str] | None = None,
                 agent_factory: Callable[[Model], Agent] | None = None,
                 level: str | None = None, audit: AuditLog | None = None) -> None:
        from champion.agents.orchestrator import build_orchestrator
        from champion.specialists.registry import select_specialists

        self.config = config
        self.audit = audit if audit is not None else AuditLog(config.get("storage.audit_db"))
        self.context = ChampionContext.from_config(config, level=level, audit=self.audit)
        self.model = model if model is not None else build_model(config, build_client(config, env))
        if agent_factory is not None:
            self.specialists, self.specialist_problems = [], []
            self.agent = agent_factory(self.model)
        else:
            self.specialists, self.specialist_problems = select_specialists(config)
            self.agent = build_orchestrator(self.model, self.specialists, config)
        self.context.specialists = [spec.tool_name for spec in self.specialists]
        self.run_config = make_run_config(config)
        self.max_turns = config.get("orchestration.max_steps")
        self.total_usage = TurnUsage()
        self.new_session()

    def new_session(self) -> None:
        self.session = SQLiteSession(f"champion-{uuid.uuid4().hex}")  # in memory only

    async def ask(self, text: str, on_tool_event: Callable[[ToolEvent], None] | None = None) -> TurnResult:
        recorder = ToolEventRecorder(on_tool_event)
        self.context.recorder = recorder  # specialists' nested tool calls report here
        try:
            result = await Runner.run(
                self.agent,
                text,
                context=self.context,
                max_turns=self.max_turns,
                hooks=recorder,
                run_config=self.run_config,
                session=self.session,
            )
        finally:
            self.context.recorder = None
        raw = result.context_wrapper.usage
        usage = TurnUsage(raw.requests, raw.input_tokens, raw.output_tokens, raw.total_tokens)
        self.total_usage.add(usage)
        return TurnResult(text=str(result.final_output), tool_events=recorder.events, usage=usage)
