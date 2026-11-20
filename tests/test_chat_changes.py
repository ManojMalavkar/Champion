"""Chat integration of step 4a: change summary, /changes, /undo, approval prompt, -q refusal."""

import asyncio
import hashlib

import pytest
from agents import Agent
from fake_model import FakeModel, text, tool_call

from champion.agents.orchestrator import stop_after_decline
from champion.chat import HELP, answer, chat_loop, pending_terminal_input
from champion.core.config import load_config
from champion.core.runtime import ChampionRuntime
from champion.storage.audit import AuditLog
from champion.tools.edit import EDIT_TOOLS
from champion.tools.shell import SHELL_TOOLS

MODEL = "*KEYWORD\n*MAT_ELASTIC\n1 7.85e-9 210000.0 0.3\n*END\n"


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    (work / "main.k").write_text(MODEL)
    (work / "old.k").write_text("old")
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def runtime_for(config, outputs, level="1.2"):
    model = FakeModel(outputs)
    return ChampionRuntime(config, model=model, audit=AuditLog(), level=level,
                           agent_factory=lambda m: Agent(name="Champion", tools=EDIT_TOOLS + SHELL_TOOLS, model=m,
                                                         tool_use_behavior=stop_after_decline))


def drive(runtime, lines):
    out, prompts = [], []
    queue = list(lines)

    def read_line(prompt):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    return "\n".join(out), prompts


def test_edit_summary_changes_undo_and_new(setup):
    config, work = setup
    runtime = runtime_for(config, [
        tool_call("file_edit", {"path": "main.k", "old_text": "210000.0", "new_text": "200000.0",
                                "expected_sha256": hashlib.sha256(MODEL.encode()).hexdigest()}), text("done"),
    ])
    out, _ = drive(runtime, ["change E to 200000", "/changes", "/new", "/changes", "/undo", "/undo", "/undo x"])
    assert "Changed files this turn: 1 (see /changes)" in out
    assert out.count(" modified ") == 2  # /changes before and after /new: the list is kept
    assert "#1: " in out and "restored" in out and (work / "main.k").read_text() == MODEL
    assert "Undo refused: nothing left to undo" in out
    assert "Use /undo or /undo N" in out
    assert "/changes" in HELP and "/undo N" in HELP


def test_delete_prompt_no_then_yes(setup):
    config, work = setup
    runtime = runtime_for(config, [
        # after the no the turn ends with Champion's fixed answer; the model is not called again
        tool_call("shell_run", {"argv": ["rm", "old.k"]}, call_id="c1"),
        tool_call("shell_run", {"argv": ["rm", "old.k"]}, call_id="c2"), text("deleted"),
    ])
    out, prompts = drive(runtime, ["delete old.k", "", "delete old.k again", "y", "/changes"])
    assert prompts.count("Approve? [y/N] ") == 2
    assert "Not done: you answered no at the approval prompt" in out
    assert "Approval needed (delete) for shell_run:" in out and "command : rm old.k" in out
    assert "not approved; nothing was run" in out and "BLOCKED: the user did not approve" in out
    assert "\n  approved" in out and not (work / "old.k").exists()
    assert " deleted " in out.split("Changes in this session")[-1]
    assert "Changed files this turn: 1 (see /changes)" in out


def test_noninteractive_question_refuses_deletes(setup):
    config, work = setup
    runtime = runtime_for(config, [tool_call("shell_run", {"argv": ["rm", "old.k"]}), text("could not")])
    lines = []
    assert asyncio.run(answer(runtime, "delete old.k", lines.append))
    assert any("needs the user's approval, which is not possible here" in line for line in lines)
    assert (work / "old.k").exists() and runtime.context.approver is None


def test_undo_is_told_to_the_model_once(setup):
    """Local 4d check: after /undo the model still believed the edited values."""
    config, work = setup
    runtime = runtime_for(config, [
        tool_call("file_edit", {"path": "main.k", "old_text": "210000.0", "new_text": "200000.0",
                                "expected_sha256": hashlib.sha256(MODEL.encode()).hexdigest()}), text("done"),
        text("E is 210000 again"), text("ok"),
    ])
    drive(runtime, ["change E to 200000", "/undo", "/undo", "what is E now?", "thanks"])
    inputs = [str(call["input"]) for call in runtime.model.calls]
    assert "the user typed /undo" not in inputs[1]
    assert "the user typed /undo" in inputs[2] and f"{work / 'main.k'} restored" in inputs[2]
    assert inputs[2].count("the user typed /undo") == 1  # the refused second /undo adds nothing
    assert inputs[3].count("the user typed /undo") == 1  # kept in history, not added again
    assert (work / "main.k").read_text() == MODEL


def drive_with_pending(runtime, lines, early):
    out, prompts, queue, early = [], [], list(lines), iter(early)

    def read_line(prompt):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append, pending=lambda: next(early, "")))
    return "\n".join(out), prompts


def test_input_pasted_before_the_approval_prompt_never_answers_it(setup):
    """Local 4d check: in a 60-line paste the line after "Run the tests" answered Approve?.
    A pasted y must never approve; only the answer typed after the prompt counts."""
    config, work = setup
    runtime = runtime_for(config, [tool_call("shell_run", {"argv": ["rm", "old.k"]}, call_id="c1")])
    # nothing waits after the chat line; "y" and "yes" were pasted and wait before the prompt
    out, prompts = drive_with_pending(runtime, ["delete old.k", "n"], ["", "y\nyes\n"])
    assert "(ignored 2 line(s) typed or pasted before this prompt; only an answer typed now counts)" in out
    assert "not approved; nothing was run" in out and (work / "old.k").exists()
    assert prompts.count("Approve? [y/N] ") == 1


def test_a_paste_is_one_message_and_runs_no_commands(setup):
    """Local 4d check: each pasted line was a message (about 45 model calls)."""
    config, work = setup
    runtime = runtime_for(config, [text("one answer")])  # a second model call would fail
    out, prompts = drive_with_pending(runtime, ["first line"], ["/new\n/exit\nsecond line\n"])
    assert "(pasted text: 4 lines taken as one message)" in out and "one answer" in out
    assert prompts == ["you> ", "you> "]  # /exit inside the paste did not end the chat


def test_pending_terminal_input_reads_a_paste_from_a_real_terminal():
    import io
    import os
    import pty

    assert pending_terminal_input(io.StringIO("y\n")) == ""  # not a terminal: nothing is read
    master, slave = pty.openpty()
    try:
        with open(slave, "r", closefd=True) as stream:
            os.write(master, b"y\nsecond\n")
            assert pending_terminal_input(stream, settle=0.2).splitlines() == ["y", "second"]
            assert pending_terminal_input(stream) == ""
    finally:
        os.close(master)
