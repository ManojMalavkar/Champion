"""Tests for the plain chat and ``champion chat`` (fake model, no network)."""

import asyncio
from pathlib import Path

import pytest
from fake_model import FakeModel, text, tool_call

from champion import cli
from champion.chat import chat_loop, describe_error, run_chat
from champion.core.config import load_config
from champion.core.runtime import ChampionRuntime


def make_runtime(tmp_path: Path, outputs, extra: str = "") -> ChampionRuntime:
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: test-model\n" + extra)
    return ChampionRuntime(load_config(folder / "config.yaml", env={}, cwd=tmp_path), model=FakeModel(outputs))


def drive(runtime, lines):
    queue, out = list(lines), []

    def read_line(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    asyncio.run(chat_loop(runtime, read_line, out.append))
    return "\n".join(out)


def test_chat_commands_and_answer(tmp_path):
    runtime = make_runtime(tmp_path, [tool_call("session_info"), text("Level 1.1.")])
    out = drive(runtime, ["/help", "what level?", "/usage", "/new", "/bogus", "/exit", "never read"])
    assert "Commands:" in out
    assert "-> session_info({})" in out
    assert "<- session_info:" in out
    assert "champion> Level 1.1." in out
    assert "240 tokens (200 in, 40 out), 2 model calls" in out
    assert "New conversation started." in out
    assert "Unknown command /bogus" in out
    assert out.endswith("Bye.")


def test_chat_ends_on_eof(tmp_path):
    assert drive(make_runtime(tmp_path, []), []).endswith("Bye.")


def test_errors_keep_the_chat_alive(tmp_path):
    # FakeModel has no output left -> the turn fails, the chat continues
    out = drive(make_runtime(tmp_path, []), ["hello", "/usage"])
    assert "Error: AssertionError" in out
    assert "0 tokens" in out


def test_max_turns_message(tmp_path):
    runtime = make_runtime(tmp_path, [tool_call("session_info", call_id=f"c{i}") for i in range(3)],
                           "orchestration:\n  max_steps: 2\n")
    assert "used all 2 turns" in drive(runtime, ["loop"])


def test_one_shot_question(tmp_path, capsys):
    assert run_chat(make_runtime(tmp_path, [text("Hi.")]), "hello") == 0
    assert "champion> Hi." in capsys.readouterr().out
    assert run_chat(make_runtime(tmp_path, []), "hello") == 1


def test_api_errors_show_type_and_status_only():
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(401, request=request)
    exc = openai.AuthenticationError("Incorrect API key provided: sk-abc", response=response, body=None)
    message = describe_error(exc)
    assert message == "AuthenticationError (HTTP 401) from the OpenAI API"
    assert "sk-abc" not in message


def test_cli_chat_without_key_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "c.yaml"
    path.write_text("llm:\n  model_id: m\n")
    assert cli.main(["--config", str(path), "chat", "-q", "hi"]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
