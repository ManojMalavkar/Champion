"""Tests for the plain chat and ``champion chat`` (fake model, no network)."""

import asyncio
from pathlib import Path

import pytest
from fake_model import FakeModel, text, tool_call

from champion import cli
from champion.chat import chat_loop, describe_error, markdown_printer, run_chat, use_markdown
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


def test_level_change_updates_orchestrator_and_specialist_instructions(tmp_path):
    runtime = make_runtime(tmp_path, [
        text("Level 1.1 is read-only."),
        tool_call("linux_coder_agent", {"input": "Write file names to a text file"}),
        text("I can use the permitted tools."),
        text("The Linux specialist checked the request."),
    ])
    out = drive(runtime, ["What can I do?", "/level 1.2", "Write file names to a text file", "/exit"])

    calls = runtime.model.calls
    assert "Access level is now 1.2" in out
    assert "Current access level: 1.1" in calls[0]["instructions"]
    assert "Current access level: 1.2" in calls[1]["instructions"]
    assert "Current access level: 1.2" in calls[2]["instructions"]
    assert calls[2]["instructions"].startswith("You are Champion's Linux specialist")
    assert "Current access level: 1.2" in calls[3]["instructions"]


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


ANSWER = "I can help with:\n- **File exploration**: list folders\n- **Session info**: level\n\nUse `/level`."


def test_markdown_answer_is_rendered_in_a_terminal(tmp_path):
    import io

    from rich.console import Console

    buffer = io.StringIO()
    show = markdown_printer(Console(file=buffer, force_terminal=True, width=80, color_system=None))
    runtime = make_runtime(tmp_path, [tool_call("session_info"), text(ANSWER)])
    out = drive_with(runtime, ["what can you do?"], show)
    rendered = buffer.getvalue()
    assert "champion>" in rendered and "File exploration" in rendered
    assert "**" not in rendered and "\u2022" in rendered  # bold markers gone, bullets drawn
    assert "-> session_info({})" in out  # tool lines stay plain
    assert "champion>" not in out and "tokens" in out


def drive_with(runtime, lines, show_answer):
    queue, out = list(lines), []

    def read_line(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    asyncio.run(chat_loop(runtime, read_line, out.append, show_answer))
    return "\n".join(out)


def test_plain_text_when_not_a_terminal(tmp_path, capsys):
    assert run_chat(make_runtime(tmp_path, [text(ANSWER)]), "hello") == 0  # capsys is not a terminal
    assert "champion> I can help with:\n- **File exploration**" in capsys.readouterr().out


def test_use_markdown_rules():
    class Stream:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    assert use_markdown(False, Stream(True)) is True
    assert use_markdown(True, Stream(True)) is False  # --plain
    assert use_markdown(False, Stream(False)) is False  # file or pipe
    assert use_markdown(False, object()) is False
    assert cli.build_parser().parse_args(["chat", "--plain"]).plain is True
    assert cli.build_parser().parse_args(["chat"]).plain is False


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
