"""Tests for the step 1 runtime, agent and tool (fake model, no network)."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.exceptions import MaxTurnsExceeded
from fake_model import FakeModel, text, tool_call

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.core.runtime import (ChampionRuntime, RuntimeSetupError, build_client, build_model,
                                   keep_last_turns, make_run_config)
from champion.tools.session_info import describe_session

SECRET = "sk-test-THIS-MUST-NEVER-BE-PRINTED"


def make_config(tmp_path: Path, extra: str = ""):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    path = folder / "config.yaml"
    path.write_text("llm:\n  model_id: test-model\n" + extra, encoding="utf-8")
    return load_config(path, env={}, cwd=tmp_path)


# ------------------------------------------------------------- setup

def test_client_uses_key_and_base_url(tmp_path):
    config = make_config(tmp_path, "  api_key_env: DATABRICKS_TOKEN\n  base_url: https://gateway.example/v1\n")
    client = build_client(config, env={"DATABRICKS_TOKEN": SECRET})
    assert client.api_key == SECRET
    assert str(client.base_url).startswith("https://gateway.example/v1")


def test_missing_key_or_model_is_a_setup_error(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(RuntimeSetupError, match="OPENAI_API_KEY"):
        build_client(config, env={})
    placeholder = make_config(tmp_path)
    placeholder.data["llm"]["model_id"] = "REPLACE_WITH_MODEL_ID"
    with pytest.raises(RuntimeSetupError, match="model_id"):
        build_model(placeholder, build_client(config, env={"OPENAI_API_KEY": SECRET}))


def test_model_class_follows_the_provider(tmp_path):
    from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel

    openai_config = make_config(tmp_path)
    client = build_client(openai_config, env={"OPENAI_API_KEY": SECRET})
    assert isinstance(build_model(openai_config, client), OpenAIResponsesModel)
    gateway = make_config(tmp_path, "  provider: DATABRICKS\n  api_key_env: DATABRICKS_TOKEN\n"
                                    "  base_url: https://adb-1.azuredatabricks.net/ai-gateway/mlflow/v1\n")
    client = build_client(gateway, env={"DATABRICKS_TOKEN": SECRET})
    assert str(client.base_url).startswith("https://adb-1.azuredatabricks.net/ai-gateway/mlflow/v1")
    assert isinstance(build_model(gateway, client), OpenAIChatCompletionsModel)


def test_tracing_off_by_default(tmp_path):
    assert make_run_config(make_config(tmp_path)).tracing_disabled is True
    assert make_run_config(make_config(tmp_path, "  tracing: true\n")).tracing_disabled is False


# ------------------------------------------------------------- history

def user(t):
    return {"role": "user", "content": t}


def test_keep_last_turns_cuts_at_user_messages():
    history = [user("q1"), {"role": "assistant", "content": "a1"},
               user("q2"), {"type": "function_call", "call_id": "c"},
               {"type": "function_call_output", "call_id": "c"}, {"role": "assistant", "content": "a2"},
               user("q3"), {"role": "assistant", "content": "a3"}]
    new = [user("q4")]
    kept = keep_last_turns(history, new, max_turns=2)
    assert kept[0] == user("q2")  # whole turn 2 kept, including tool call and result
    assert kept[-1] == user("q4")
    assert len(kept) == 7
    assert keep_last_turns(history, new, max_turns=10) == history + new


# ------------------------------------------------------------- the agent loop

def test_tool_call_then_answer(tmp_path):
    model = FakeModel([tool_call("session_info"), text("You are at level 1.1.")])
    runtime = ChampionRuntime(make_config(tmp_path), model=model)
    seen = []
    turn = asyncio.run(runtime.ask("What is my access level?", on_tool_event=seen.append))

    assert turn.text == "You are at level 1.1."
    assert [e.phase for e in seen] == ["start", "end"]
    assert turn.tool_events[0].tool == "session_info"
    assert turn.usage.requests == 2 and turn.usage.total_tokens == 240
    # first call: the agent's instructions and its tools are sent to the model
    assert "You are Champion" in model.calls[0]["instructions"]
    assert model.calls[0]["tools"] == ["session_info", "filesystem_list", "filesystem_read",
                                        "filesystem_disk_usage", "coder_agent",  # Coder since step 4d.6
                                        "linux_coder_agent"]  # Linux since step 4b
    # second call: the tool result was sent back to the model
    outputs = [i for i in model.calls[1]["input"] if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert json.loads(outputs[0]["output"])["access_level"] == "1.1"


def test_session_remembers_and_new_session_forgets(tmp_path):
    model = FakeModel([text("Hello Manoj."), text("Your name is Manoj."), text("I don't know.")])
    runtime = ChampionRuntime(make_config(tmp_path), model=model)

    async def chat():
        await runtime.ask("My name is Manoj")
        await runtime.ask("What is my name?")
        runtime.new_session()
        await runtime.ask("What is my name?")

    asyncio.run(chat())
    second, third = model.calls[1]["input"], model.calls[2]["input"]
    assert any("My name is Manoj" in str(i) for i in second)
    assert not any("My name is Manoj" in str(i) for i in third)
    assert runtime.total_usage.requests == 3


def test_history_limit_is_applied(tmp_path):
    model = FakeModel([text(f"a{i}") for i in range(4)])
    runtime = ChampionRuntime(make_config(tmp_path, "orchestration:\n  history_turns: 1\n"), model=model)

    async def chat():
        for i in range(4):
            await runtime.ask(f"question {i}")

    asyncio.run(chat())
    last_input = str(model.calls[3]["input"])
    assert "question 2" in last_input and "question 3" in last_input
    assert "question 1" not in last_input and "question 0" not in last_input


def test_max_turns_stops_a_looping_agent(tmp_path):
    model = FakeModel([tool_call("session_info", call_id=f"c{i}") for i in range(5)])
    runtime = ChampionRuntime(make_config(tmp_path, "orchestration:\n  max_steps: 3\n"), model=model)
    with pytest.raises(MaxTurnsExceeded):
        asyncio.run(runtime.ask("loop"))
    assert len(model.calls) == 3


def test_session_info_content(tmp_path):
    config = make_config(tmp_path, "policy:\n  default_level: '2.1'\n")
    info = describe_session(ChampionContext.from_config(config))
    assert info["access_level"] == "2.1"
    assert info["access_level_enforced"] is True
    assert "working folder read, everything else read" in info["access"]
    assert info["working_directory"] == str(tmp_path.resolve())
    assert info["enabled_specialists"] == ["coder", "linux_coder"]
    assert SECRET not in json.dumps(info)
