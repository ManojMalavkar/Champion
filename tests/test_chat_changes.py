"""Chat integration of step 4a: change summary, /changes, /undo, approval prompt, -q refusal."""

import asyncio

import pytest
from agents import Agent
from fake_model import FakeModel, text, tool_call

from champion.chat import HELP, answer, chat_loop
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
                           agent_factory=lambda m: Agent(name="Champion", tools=EDIT_TOOLS + SHELL_TOOLS, model=m))


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
        tool_call("file_edit", {"path": "main.k", "old_text": "210000.0", "new_text": "200000.0"}), text("done"),
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
        tool_call("shell_run", {"argv": ["rm", "old.k"]}, call_id="c1"), text("not deleted"),
        tool_call("shell_run", {"argv": ["rm", "old.k"]}, call_id="c2"), text("deleted"),
    ])
    out, prompts = drive(runtime, ["delete old.k", "", "delete old.k again", "y", "/changes"])
    assert prompts.count("Approve? [y/N] ") == 2
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
