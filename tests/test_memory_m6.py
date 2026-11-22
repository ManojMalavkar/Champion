"""CR-2 (M6): the memory hook on main - config, runtime, guard, approval, instructions, /memory, doctor."""

import asyncio
import functools
import json
import os
import time

import pytest
from agents import Agent, RunContextWrapper
from fake_model import FakeModel, text, tool_call

from champion.agents.orchestrator import stop_after_decline, with_current_level
from champion.chat import HELP, answer, chat_loop
from champion.core.config import ConfigError, load_config
from champion.core.runtime import ChampionRuntime, keep_last_turns
from champion.doctor import check_memory
from champion.memory.commands import OFF, memory_command
from champion.memory.hook import MEMORY_RULE, SAVE_RULE
from champion.memory.runtime_state import RuntimeStateStore
from champion.memory.summaries import SummaryStore
from champion.storage.audit import AuditLog
from champion.tools.edit import EDIT_TOOLS
from champion.tools.shell import SHELL_TOOLS

SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUV123456"
SUMMARY = json.dumps({"topic": "scripts", "decisions": [{"text": "Keep the scripts in the tools folder",
                                                        "reason": "the user said so"}]})


def make_config(tmp_path, memory="", extra=""):
    work = (tmp_path / "work").resolve()
    work.mkdir(exist_ok=True)
    (work / "old.k").write_text("old")
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    body = f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n{extra}"
    if memory is not None:
        body += f"memory:\n  enabled: true\n  user_root: '{tmp_path / 'user-memory'}'\n{memory}"
    (folder / "config.yaml").write_text(body)
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def runtime_for(config, outputs, level="1.2"):
    model = FakeModel(outputs)
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(), level=level,
                              agent_factory=lambda m: Agent(name="Champion", tools=EDIT_TOOLS + SHELL_TOOLS, model=m,
                                                            instructions=with_current_level("Base."),
                                                            tool_use_behavior=stop_after_decline))
    return runtime, model


def archived(work):
    folder = work / ".champion" / "runtime" / "archive"
    return [json.loads(f.read_text()) for f in sorted(folder.glob("REQ-*.json"))]


def active_files(work):
    return sorted((work / ".champion" / "runtime").glob("REQ-*.json"))


# ------------------------------------------------------------------ memory off

def test_memory_off_by_default_changes_nothing(tmp_path):
    config, work = make_config(tmp_path, memory=None)
    assert config.get("memory.enabled") is False
    runtime, model = runtime_for(config, [tool_call("shell_run", {"argv": ["ls"]}), text("ok")])
    assert runtime.memory is None and runtime.context.memory is None
    callback = runtime.run_config.session_input_callback
    assert isinstance(callback, functools.partial) and callback.func is keep_last_turns
    assert asyncio.run(runtime.ask("list /data/x/model.k")).text == "ok"
    assert not (work / ".champion").exists()  # no runtime state
    assert "Memory notes" not in model.calls[0]["instructions"]
    assert memory_command(runtime, "") == OFF and memory_command(runtime, "set unit_system x") == OFF
    assert check_memory(config).detail.startswith("off")
    assert "/memory" in HELP


# ---------------------------------------------------------------------- config

@pytest.mark.parametrize("memory, message", [
    ("  max_turns: 0\n", "memory.max_turns must be a whole number from 1 to 50"),
    ("  max_tokens: 10\n", "memory.max_tokens"),
    ("  keep_archived: -1\n", "memory.keep_archived"),
    ("  summaries: 'yes'\n", "memory.summaries must be true or false"),
])
def test_config_memory_is_validated(tmp_path, memory, message):
    with pytest.raises(ConfigError, match=message):
        make_config(tmp_path, memory=memory)


def test_config_memory_paths(tmp_path):
    config, _ = make_config(tmp_path, memory=None, extra="memory:\n  project: proj\n")
    assert config.get("memory.user_root") == os.path.realpath(os.path.expanduser("~")) + "/.champion/memory"
    assert config.get("memory.project") == os.path.realpath(tmp_path / "proj")  # relative: from the current folder


# ------------------------------------------------------------ L0 lifecycle

def test_turn_is_one_request_done_with_tool_calls_and_audit_id(tmp_path):
    config, work = make_config(tmp_path)
    runtime, _ = runtime_for(config, [tool_call("shell_run", {"argv": ["ls"]}), text("listed")])
    assert asyncio.run(runtime.ask("list the folder")).text == "listed"
    assert active_files(work) == []  # ended: moved to the archive
    [state] = archived(work)
    assert state["current_step"] == "done" and state["finished_at"] and state["agent"] == "Champion"
    [call] = state["tool_calls"]
    assert call["tool"] == "shell_run" and call["arguments"]["argv"] == ["ls"] and call["decision"] == "allow"
    assert isinstance(call["audit_id"], int) and call["audit_id"] > 0
    assert "result_excerpt" not in call  # archived without result excerpts
    assert state["session_id"] == runtime.session.session_id


def test_failed_and_cancelled_requests(tmp_path):
    config, work = make_config(tmp_path)
    runtime, model = runtime_for(config, [])  # no scripted output: the model call fails
    with pytest.raises(AssertionError):
        asyncio.run(runtime.ask("hello"))

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError
    model.get_response = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.ask("hello again"))
    failed, stopped = archived(work)
    assert failed["current_step"] == "failed" and "AssertionError" in failed["errors"][-1]["message"]
    assert stopped["current_step"] == "cancelled" and stopped["cancelled"] is True
    assert active_files(work) == []


def test_approval_is_recorded_and_never_answered_by_memory(tmp_path):
    config, work = make_config(tmp_path)
    runtime, _ = runtime_for(config, [tool_call("shell_run", {"argv": ["rm", "old.k"]})])
    seen = {}

    def approver(request):
        memory = runtime.memory
        seen["working"] = list(memory.working.pending_approvals)
        seen["step"] = memory.state.current_step
        return False
    runtime.context.approver = approver
    asyncio.run(runtime.ask("delete old.k"))
    assert seen["step"] == "awaiting_approval"
    assert seen["working"] == ["shell_run (delete): rm old.k"]
    assert runtime.memory.working.pending_approvals == []  # removed after the answer
    [state] = archived(work)
    [approval] = state["pending_approvals"]
    assert approval["answer"] == "declined" and "rm old.k" in approval["summary"]
    assert state["tool_calls"][-1]["decision"] == "block" and (work / "old.k").exists()


# -------------------------------------------------------------- instructions

def test_instruction_blocks_for_orchestrator_and_specialists(tmp_path):
    config, work = make_config(tmp_path)
    runtime, model = runtime_for(config, [text("ok")])
    assert memory_command(runtime, "set result_format csv") == "Preference result_format = csv"
    asyncio.run(runtime.ask("check /data/models/car.k part 12"))
    instructions = model.calls[0]["instructions"]
    assert MEMORY_RULE in instructions and SAVE_RULE in instructions
    assert "[User preferences" in instructions and "- result_format: csv" in instructions
    assert "[Working memory of this task]" in instructions and "/data/models/car.k" in instructions
    render = with_current_level("Specialist.")
    wrapper = RunContextWrapper(context=runtime.context)
    specialist = render(wrapper, Agent(name="Linux"))
    assert "result_format: csv" in specialist and "Working memory" not in specialist
    assert "[Working memory of this task]" in render(wrapper, Agent(name="Champion"))


def test_orchestrator_is_told_it_cannot_save_memory(tmp_path):
    """Live check 22 Nov 2026: with no notes yet the model answered 'Memory note set: ...'."""
    config, _ = make_config(tmp_path)
    runtime, _ = runtime_for(config, [])
    orchestrator = runtime.memory.instruction_blocks(orchestrator=True)
    assert SAVE_RULE in orchestrator and "[User preferences" not in orchestrator  # nothing stored yet
    assert runtime.memory.instruction_blocks(orchestrator=False) == ""  # specialists: no notes, no rule


# ------------------------------------------------------ history and summaries

def test_history_selection_and_summary_of_dropped_turns(tmp_path):
    config, work = make_config(tmp_path, memory="  max_turns: 1\n")
    runtime, model = runtime_for(config, [text("a1"), text("a2"), text("a3"), text(SUMMARY)])
    asyncio.run(runtime.ask("first question"))
    asyncio.run(runtime.ask("second question"))
    turn = asyncio.run(runtime.ask("third question"))
    users = [item for item in model.calls[2]["input"] if isinstance(item, dict) and item.get("role") == "user"]
    assert [u["content"] for u in users] == ["second question", "third question"]  # only max_turns=1 earlier turn
    assert turn.text == "a3" and turn.usage.requests == 2  # the turn plus its summary call
    assert "first question" in str(model.calls[3]["input"])  # the dropped turn was summarised
    [(summary_id, index, item)] = SummaryStore(tmp_path / "user-memory" / "summaries").active_items()
    assert item.text == "Keep the scripts in the tools folder"
    assert "[Earlier decisions" in runtime.memory.instruction_blocks(orchestrator=True)
    assert f"{summary_id}#0" in memory_command(runtime, "summaries")
    assert "is now done" in memory_command(runtime, f"done {summary_id}#0")
    assert memory_command(runtime, "summaries") == "No active summary items."


def test_failing_summary_never_fails_the_turn_and_is_not_retried(tmp_path):
    config, work = make_config(tmp_path, memory="  max_turns: 1\n")
    runtime, model = runtime_for(config, [text("a1"), text("a2"), text("a3"), text("not json"), text("a4"),
                                          text(SUMMARY)])
    for question in ("one", "two"):
        asyncio.run(runtime.ask(question))
    turn = asyncio.run(runtime.ask("three"))  # turn "one" is dropped; its summary answer is not JSON
    assert turn.text == "a3"
    assert any("summary not saved" in p for p in runtime.memory.problems)
    assert "problem      : summary not saved" in memory_command(runtime, "")
    assert runtime.memory.pending_turns() == []
    assert asyncio.run(runtime.ask("four")).text == "a4"  # now turn "two" is dropped too
    summary_input = str(model.calls[-1]["input"])
    assert "two" in summary_input and "'one'" not in summary_input  # "one" is not summarised again


# ------------------------------------------------------------------- secrets

def test_secrets_are_not_stored_in_memory_files(tmp_path):
    config, work = make_config(tmp_path, memory="  max_turns: 1\n")
    runtime, _ = runtime_for(config, [tool_call("shell_run", {"argv": ["ls", f"--key={SECRET}"]}), text("a1"),
                                      text("a2"), text(json.dumps({"topic": "t", "decisions": [
                                          {"text": "Keep the scripts in the tools folder"}]}))])
    asyncio.run(runtime.ask(f"my token is {SECRET}, list /data/secret-turn.k"))
    asyncio.run(runtime.ask("next: /data/a.k"))
    asyncio.run(runtime.ask("third"))  # summarises the first turn
    files = [p for root in (work / ".champion", tmp_path / "user-memory") for p in root.rglob("*") if p.is_file()]
    assert files and all(SECRET not in p.read_text() for p in files)
    rendered = runtime.memory.working.render()
    assert SECRET not in rendered and "/data/a.k" in rendered
    assert "/data/secret-turn.k" not in rendered  # a message with a secret is not used at all


# ------------------------------------------------------------ /memory, chat

def test_memory_commands(tmp_path):
    config, work = make_config(tmp_path)
    runtime, _ = runtime_for(config, [])
    assert "Memory is on." in memory_command(runtime, "") and "/memory set KEY VALUE" in memory_command(runtime, "help")
    assert "result_format" in memory_command(runtime, "keys")
    assert memory_command(runtime, "set working_locations /data/a, ~/b") == "Preference working_locations = ['/data/a', '~/b']"
    assert memory_command(runtime, "set display_units force=kN, length=mm").endswith("{'force': 'kN', 'length': 'mm'}")
    assert "Memory refused" in memory_command(runtime, "set destructive_action_confirmation false")
    assert "Memory refused" in memory_command(runtime, f"set hpc_gateway {SECRET}")
    assert "Memory refused: unknown preference" in memory_command(runtime, "set nope 1")
    assert "working_locations" in memory_command(runtime, "prefs")
    assert memory_command(runtime, "forget working_locations") == "Preference working_locations removed."
    assert memory_command(runtime, "show").startswith(MEMORY_RULE)
    # project folder: needs edit access (working folder at 1.2: yes; /etc: no)
    assert "Project memory refused" in memory_command(runtime, "project /etc")
    assert memory_command(runtime, f"project {work}").startswith(f"Project memory folder: {work}")
    assert runtime.memory.summaries.folder == work / ".champion" / "memory" / "summaries"
    assert memory_command(runtime, "project off").startswith("Project memory off")
    runtime.memory.working.note_entities("/data/x.k")
    runtime.new_session()  # /new
    assert runtime.memory.working.render() == ""
    assert memory_command(runtime, "what").startswith("Unknown memory command what")


def test_chat_lists_unfinished_requests_and_closes_them(tmp_path):
    config, work = make_config(tmp_path)
    left = RuntimeStateStore.for_working_directory(work).start(agent="Champion")  # a chat that was killed
    runtime, _ = runtime_for(config, [])
    out = []
    lines = ["/memory unfinished", "/memory unfinished close", "/memory"]

    def read_line(prompt):
        if not lines:
            raise EOFError
        return lines.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    text_out = "\n".join(out)
    assert f"1 earlier request(s) in this folder never ended ({left.request_id})" in text_out
    assert f"Marked failed: {left.request_id}" in text_out
    assert archived(work)[0]["current_step"] == "failed" and active_files(work) == []


def test_doctor_memory_line(tmp_path):
    config, _ = make_config(tmp_path)
    result = check_memory(config)
    assert result.status == "OK" and result.detail.startswith("on: runtime state, summaries, preferences")


def test_empty_request_file_of_a_killed_chat_is_listed_and_closed(tmp_path):
    """Live check 22 Nov 2026: a chat killed between claiming the ID and its first write left a
    0-byte REQ file (and a .tmp file); /memory unfinished listed nothing."""
    config, work = make_config(tmp_path)
    folder = work / ".champion" / "runtime"
    folder.mkdir(parents=True)
    empty, temp = folder / "REQ-20261122-0008.json", folder / ".tmp-s6tsevrw.json"
    empty.write_text("")
    temp.write_text('{"request_id": "REQ-20261122-0008"}')
    young = folder / "REQ-20261122-0009.json"
    young.write_text("")  # another chat that is just starting: not listed, not touched
    old = time.time() - 7200
    os.utime(empty, (old, old))
    os.utime(temp, (old, old))
    runtime, _ = runtime_for(config, [])
    assert [s.request_id for s in runtime.memory.unfinished()] == ["REQ-20261122-0008"]
    out = []
    lines = ["/memory unfinished close"]

    def read_line(prompt):
        if not lines:
            raise EOFError
        return lines.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    text_out = "\n".join(out)
    assert "1 earlier request(s) in this folder never ended (REQ-20261122-0008)" in text_out
    assert "Marked failed: REQ-20261122-0008" in text_out
    [state] = archived(work)
    assert state["current_step"] == "failed" and "empty or unreadable" in state["errors"][0]["message"]
    assert not empty.exists() and not temp.exists() and young.exists()


def test_old_temp_files_are_removed_at_start(tmp_path):
    """Live check 22 Nov 2026: the .tmp file stayed when the user did not run /memory unfinished close."""
    config, work = make_config(tmp_path)
    folder = work / ".champion" / "runtime"
    folder.mkdir(parents=True)
    old, young = folder / ".tmp-old.json", folder / ".tmp-young.json"
    old.write_text("{}")
    young.write_text("{}")  # a write of another chat that is running now
    then = time.time() - 600
    os.utime(old, (then, then))
    runtime_for(config, [])
    assert not old.exists() and young.exists()


def test_nested_calls_carry_the_sub_agent_name(tmp_path):
    config, work = make_config(tmp_path)
    runtime, _ = runtime_for(config, [])
    memory = runtime.memory
    memory.start_request("task", agent="Champion", access="1.2")
    for agent in ("PyDyna/Keyword", "Linux", "Champion", ""):
        memory.tool_call(tool="filesystem_read", arguments={"path": "/data/a.k"}, decision="allow", result="x",
                         reason="", audit_id=None, agent=agent)
    assert [c["agent"] for c in memory.state.tool_calls] == ["PyDyna/Keyword", "Linux", "Champion", "Champion"]
    memory.finish_request("done")
    assert archived(work)[0]["current_step"] == "done"


def test_chat_notes_a_failing_summary_once(tmp_path):
    config, work = make_config(tmp_path, memory="  max_turns: 1\n")
    runtime, _ = runtime_for(config, [text("a1"), text("a2"), text("a3"), text("not json")])
    out = []
    for question in ("one", "two", "three"):
        assert asyncio.run(answer(runtime, question, out.append))
    notes = [line for line in out if line.startswith("  Memory: summary not saved")]
    assert len(notes) == 1 and "champion> a3" in out  # only in turn three, and the answer is shown
