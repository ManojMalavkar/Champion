"""T9.3: Conversation tab (Textual pilot, headless, fake model, no network)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("textual")

from fake_model import FakeModel, text, tool_call  # noqa: E402

from champion.core.config import load_config  # noqa: E402
from champion.core.runtime import ChampionRuntime  # noqa: E402
from champion.tui.app import ChampionApp  # noqa: E402
from champion.tui.conversation import (ActionBlock, AnswerMessage, ChatInput, ConversationView, NoteLine,  # noqa: E402
                                       UserMessage, command_word)
from champion.tui.logo import LogoState  # noqa: E402
from champion.tui.widgets import Panel  # noqa: E402

FIXED = datetime(2026, 11, 22, 10, 0, 0)


class SlowModel(FakeModel):
    """Waits until cancelled (for Ctrl+C)."""

    def __init__(self) -> None:
        super().__init__([])
        self.entered = False

    async def get_response(self, *args, **kwargs):
        self.entered = True
        await asyncio.sleep(60)
        raise AssertionError("not cancelled")


def make_app(tmp_path: Path, model) -> ChampionApp:
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: test-model\n")
    runtime = ChampionRuntime(load_config(folder / "config.yaml", env={}, cwd=tmp_path), model=model)
    return ChampionApp(runtime, clock=lambda: FIXED, logo_path=None)


def run_app(app, check, size=(140, 40)):
    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await check(app, pilot)
    asyncio.run(go())


async def send(app, pilot, message: str) -> None:
    app.query_one(ChatInput).text = message
    await pilot.press("enter")
    await pilot.pause()


async def wait_turn(app, pilot, timeout: float = 10.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if not app.busy:
            break
        await asyncio.sleep(0.05)
    await pilot.pause()
    assert not app.busy, "turn did not finish"


def items(app, kind):
    return [w for w in app.query_one(ConversationView).messages() if isinstance(w, kind)]


def test_tui_question_right_answer_left_no_labels(tmp_path):
    model = FakeModel([text("**Found** 4 duplicate PART IDs.")])
    app = make_app(tmp_path, model)

    async def check(app, pilot):
        assert app.focused is app.query_one(ChatInput)
        await send(app, pilot, "Find duplicate PART IDs")
        await wait_turn(app, pilot)
        [user] = items(app, UserMessage)
        [reply] = items(app, AnswerMessage)
        assert user.message == "Find duplicate PART IDs" and user.styles.text_align == "right"
        assert reply.message == "**Found** 4 duplicate PART IDs."
        assert reply.region.x < user.region.x + user.region.width - len(user.message)  # answer starts further left
        notes = [n.message for n in items(app, NoteLine)]
        assert notes == ["[120 tokens, 1 model call(s)]"]  # the chat's own tokens line
        shown = " ".join([user.message, reply.message, *notes])
        assert "you>" not in shown and "champion>" not in shown  # no speaker labels
        assert items(app, ActionBlock) == []  # no tool call, no action block
        assert app.query_one("#tokens", Panel).plain == "Total Tokens: 120"
        assert app.query_one(LogoState).state == "idle"
        assert app.query_one(ChatInput).text == "" and not app.query_one(ChatInput).disabled

    run_app(app, check)


def test_tui_tool_calls_shown_in_action_block(tmp_path):
    model = FakeModel([tool_call("session_info"), text("Level 1.1.")])
    app = make_app(tmp_path, model)
    statuses = []
    original = app.set_status

    def spy(status):
        statuses.append(status)
        original(status)

    app.set_status = spy

    async def check(app, pilot):
        await send(app, pilot, "Which access level?")
        await wait_turn(app, pilot)
        [block] = items(app, ActionBlock)
        first, second = (line.strip() for line in block.lines)  # chat.format_event lines
        assert first.startswith("-> session_info(") and second.startswith("<- session_info:")
        assert block.header.startswith("✓ Completed") and "1 tool call" in block.header
        assert [a.message for a in items(app, AnswerMessage)] == ["Level 1.1."]
        assert statuses[0] == "thinking" and "working" in statuses and statuses[-1] == "idle"

    run_app(app, check)


def test_tui_error_keeps_app_usable_and_logo_red(tmp_path):
    model = FakeModel([])  # no scripted output: the model raises
    app = make_app(tmp_path, model)

    async def check(app, pilot):
        await send(app, pilot, "hello")
        await wait_turn(app, pilot)
        errors = [n for n in items(app, NoteLine) if n.kind == "error"]
        assert errors and errors[0].message.startswith("Error: AssertionError")
        assert app.query_one(LogoState).state == "error"
        assert "error" in app.query_one("#title", Panel).plain
        model.outputs.append(text("ok now"))
        await send(app, pilot, "again")
        await wait_turn(app, pilot)
        assert [a.message for a in items(app, AnswerMessage)] == ["ok now"]
        assert app.query_one(LogoState).state == "idle"

    run_app(app, check)


def test_tui_ctrl_c_cancels_running_request(tmp_path):
    model = SlowModel()
    app = make_app(tmp_path, model)

    async def check(app, pilot):
        await send(app, pilot, "long job")
        for _ in range(100):
            if model.entered:
                break
            await asyncio.sleep(0.05)
        assert app.busy and app.query_one(ChatInput).disabled
        assert "Ctrl+C" in app.query_one("#input-hint", Panel).plain
        await pilot.press("ctrl+c")
        await wait_turn(app, pilot, timeout=5)
        assert any(n.kind == "stopped" and "Stopped by the user" in n.message for n in items(app, NoteLine))
        assert app.query_one(LogoState).state == "idle"
        assert not app.query_one(ChatInput).disabled
        assert app.is_running  # Ctrl+C did not quit the app

    run_app(app, check)


def test_tui_enter_sends_newline_keys_add_lines(tmp_path):
    model = FakeModel([text("two lines received")])
    app = make_app(tmp_path, model)

    async def check(app, pilot):
        await pilot.press("a", "ctrl+j", "b", "shift+enter", "c")
        assert app.query_one(ChatInput).text == "a\nb\nc"
        assert items(app, UserMessage) == []  # nothing sent yet
        await pilot.press("enter")
        await wait_turn(app, pilot)
        assert [u.message for u in items(app, UserMessage)] == ["a\nb\nc"]  # one message

    run_app(app, check)


def test_tui_up_down_recall_history(tmp_path):
    model = FakeModel([text("one"), text("two")])
    app = make_app(tmp_path, model)

    async def check(app, pilot):
        chat_input = app.query_one(ChatInput)
        await send(app, pilot, "first")
        await wait_turn(app, pilot)
        await send(app, pilot, "second")
        await wait_turn(app, pilot)
        await pilot.press("up")
        assert chat_input.text == "second"
        await pilot.press("up")
        assert chat_input.text == "first"
        await pilot.press("down")
        assert chat_input.text == "second"
        await pilot.press("down")
        assert chat_input.text == ""

    run_app(app, check)


def test_tui_slash_commands_not_sent_to_model(tmp_path):
    model = FakeModel([])
    app = make_app(tmp_path, model)

    async def check(app, pilot):
        await send(app, pilot, "/access")
        assert model.calls == [] and not app.busy
        assert any("slash commands come later" in n.message for n in items(app, NoteLine))

    run_app(app, check)
    assert command_word("/access") == "/access"
    assert command_word("/CAE/M250/model what is in it?") is None  # a path is a question
    assert command_word("/access\nmore") is None


def test_tui_approval_needed_is_refused_until_t9_4(tmp_path):
    model = FakeModel([tool_call("linux_coder_agent", {"input": "delete old.k"}),  # orchestrator -> Linux
                       tool_call("shell_run", {"argv": ["rm", "old.k"]}),  # Linux tries the delete
                       text("not deleted: approval not possible"), text("could not delete")])
    app = make_app(tmp_path, model)
    (tmp_path / "old.k").write_text("x")

    async def check(app, pilot):
        assert app.runtime.context.approver is None
        await send(app, pilot, "delete old.k")
        await wait_turn(app, pilot)
        [block] = items(app, ActionBlock)
        assert any("[Linux]" in line and "<- shell_run: BLOCKED" in line for line in block.lines), block.lines
        assert "Linux" in block.header

    run_app(app, check)
    assert (tmp_path / "old.k").exists()  # nothing was deleted
