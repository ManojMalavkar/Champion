"""Contract tests for the specialist plug-in interface and the orchestrator (step 3).

A dummy specialist with one tool is enough to check the whole path: it is
offered to the orchestrator as ``dummy_agent``, runs as a nested run with only
its own tools and the same context, and is shown and audited. All with the
scripted fake model, no network.
"""

import asyncio
import json
from pathlib import Path

import pytest
from agents import RunContextWrapper, function_tool
from fake_model import FakeModel, text, tool_call

from champion.agents.orchestrator import build_orchestrator, orchestrator_instructions
from champion.chat import chat_loop
from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.core.runtime import ChampionRuntime
from champion.policy.tool_policy import ToolRule, register
from champion.specialists.base import SpecialistSpec
from champion.specialists.registry import (check_spec, register_specialist, select_specialists,
                                           unregister_specialist)
from champion.tools.filesystem import filesystem_read
from champion.tools.guard import run_guarded
from champion.tools.session_info import describe_session

register(ToolRule("dummy_echo", "none", "Test tool: echoes its text and the access level"))


@function_tool
async def dummy_echo(ctx: RunContextWrapper[ChampionContext], text: str) -> str:
    """Echo the text and the current access level."""
    return await run_guarded(ctx, "dummy_echo", {"text": text},
                             lambda: f"echo:{text} level:{ctx.context.level.name}")


@function_tool
async def dummy_unruled(ctx: RunContextWrapper[ChampionContext]) -> str:
    """A tool nobody registered a policy entry for."""
    return "should never run"


DUMMY = SpecialistSpec(
    name="dummy",
    title="Dummy",
    description="Echoes text back; for tests of the plug-in interface.",
    instructions="You are the dummy specialist. Use dummy_echo.",
    tools=(dummy_echo, filesystem_read),
    routing_hints=("echo hello",),
)


@pytest.fixture
def dummy():
    register_specialist(DUMMY)
    yield DUMMY
    unregister_specialist("dummy")


# The dummy contract tests switch the built-in Linux specialist off, so only the dummy is offered.
LINUX_OFF = "agents:\n  linux_coder:\n    enabled: false\n"


def make_config(tmp_path: Path, extra: str = ""):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: test-model\n" + extra)
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path)


def make_runtime(tmp_path: Path, outputs, extra: str = "") -> ChampionRuntime:
    return ChampionRuntime(make_config(tmp_path, extra), model=FakeModel(outputs))


def drive(runtime, lines):
    queue, out = list(lines), []

    def read_line(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    asyncio.run(chat_loop(runtime, read_line, out.append))
    return "\n".join(out)


# ------------------------------------------------------------------ the spec

def test_good_spec_has_no_problems():
    assert check_spec(DUMMY) == []
    assert DUMMY.tool_name == "dummy_agent"


@pytest.mark.parametrize("changes, problem", [
    ({"tools": (dummy_echo, dummy_unruled)}, "tool dummy_unruled has no policy entry"),
    ({"tools": (dummy_echo, dummy_echo)}, "tool dummy_echo is listed twice"),
    ({"name": "Bad-Name"}, "name must be"),
    ({"instructions": "  "}, "instructions is empty"),
    ({"description": ""}, "description is empty"),
    ({"max_turns": 0}, "max_turns must be"),
])
def test_spec_problems(changes, problem):
    spec = SpecialistSpec(**{**DUMMY.__dict__, **changes})
    assert any(problem in p for p in check_spec(spec)), check_spec(spec)


def test_register_same_name_twice_with_a_different_spec_fails(dummy):
    register_specialist(DUMMY)  # same spec again is fine
    with pytest.raises(ValueError):
        register_specialist(SpecialistSpec(**{**DUMMY.__dict__, "title": "Other"}))


def test_select_follows_config_and_rejects_unsafe_specs(tmp_path):
    unsafe = SpecialistSpec(**{**DUMMY.__dict__, "name": "unsafe", "tools": (dummy_unruled,)})
    selected, problems = select_specialists(make_config(tmp_path), [DUMMY, unsafe])
    assert selected == [DUMMY]
    assert problems == ["specialist unsafe not offered: tool dummy_unruled has no policy entry"]

    off = make_config(tmp_path, "agents:\n  dummy:\n    enabled: false\n")
    assert select_specialists(off, [DUMMY]) == ([], [])
    off_by_default = SpecialistSpec(**{**DUMMY.__dict__, "enabled_by_default": False})
    assert select_specialists(make_config(tmp_path), [off_by_default]) == ([], [])


def test_linux_is_the_builtin_specialist(tmp_path):
    selected, problems = select_specialists(make_config(tmp_path))
    assert [s.name for s in selected] == ["linux_coder"] and problems == []
    assert select_specialists(make_config(tmp_path, LINUX_OFF)) == ([], [])


# ---------------------------------------------------------- the orchestrator

def test_orchestrator_without_specialists_says_so():
    instructions = orchestrator_instructions([])
    assert "none are available" in instructions
    agent = build_orchestrator(FakeModel([]))
    assert [t.name for t in agent.tools] == ["session_info", "filesystem_list", "filesystem_read",
                                             "filesystem_disk_usage"]


def test_specialist_is_offered_to_the_orchestrator(tmp_path, dummy):
    runtime = make_runtime(tmp_path, [text("Hi.")], LINUX_OFF)
    asyncio.run(runtime.ask("hello"))
    first = runtime.model.calls[0]
    assert first["tools"] == ["session_info", "filesystem_list", "filesystem_read",
                              "filesystem_disk_usage", "dummy_agent"]
    assert "dummy_agent (Dummy): Echoes text back" in first["instructions"]
    assert '"echo hello"' in first["instructions"]
    assert runtime.context.specialists == ["dummy_agent"]
    assert describe_session(runtime.context)["specialists_available_now"] == ["dummy_agent"]


def test_rejected_specialist_is_not_offered_and_the_chat_warns(tmp_path):
    unsafe = SpecialistSpec(**{**DUMMY.__dict__, "name": "unsafe", "tools": (dummy_unruled,)})
    register_specialist(unsafe)
    try:
        runtime = make_runtime(tmp_path, [text("Hi.")], LINUX_OFF)
        out = drive(runtime, ["hello"])
    finally:
        unregister_specialist("unsafe")
    assert "unsafe_agent" not in runtime.model.calls[0]["tools"]
    assert "specialists: none yet" in out
    assert "Warning: specialist unsafe not offered: tool dummy_unruled has no policy entry" in out


NESTED = [
    tool_call("dummy_agent", {"input": "echo hi"}, call_id="c1"),  # orchestrator -> specialist
    tool_call("dummy_echo", {"text": "hi"}, call_id="c2"),  # specialist -> its tool
    text("The echo said hi."),  # specialist's answer
    text("Dummy reports: hi."),  # orchestrator's reply
]


def test_nested_run_uses_only_the_specialists_tools(tmp_path, dummy):
    runtime = make_runtime(tmp_path, list(NESTED))
    out = drive(runtime, ["please echo hi", "/audit"])
    calls = runtime.model.calls
    assert len(calls) == 4
    # The specialist's model call: its own tools and instructions, only the task as input.
    assert calls[1]["tools"] == ["dummy_echo", "filesystem_read"]
    assert calls[1]["instructions"].startswith("You are the dummy specialist.")
    assert "Never try to get around a block" in calls[1]["instructions"]
    assert "please echo hi" not in json.dumps(calls[1]["input"], default=str)
    assert "echo hi" in json.dumps(calls[1]["input"], default=str)
    # The tool result reaches the specialist, its answer reaches the orchestrator.
    assert "echo:hi level:1.1" in json.dumps(calls[2]["input"], default=str)
    assert "The echo said hi." in json.dumps(calls[3]["input"], default=str)
    # The chat shows both levels of tool calls and the final reply.
    assert '  -> dummy_agent({"input": "echo hi"})' in out
    assert '      [Dummy] -> dummy_echo({"text": "hi"})' in out
    assert "      [Dummy] <- dummy_echo:" in out
    assert "  <- dummy_agent:" in out
    assert "champion> Dummy reports: hi." in out


def test_specialist_calls_are_audited_with_the_agent_name(tmp_path, dummy):
    runtime = make_runtime(tmp_path, list(NESTED))
    asyncio.run(runtime.ask("please echo hi"))
    rows = runtime.audit.recent(10)
    by_tool = {r["tool"]: r for r in rows}
    assert set(by_tool) == {"dummy_echo", "dummy_agent"}
    assert by_tool["dummy_echo"]["agent"] == "Dummy"
    assert by_tool["dummy_agent"]["agent"] == "Champion"
    assert all(r["decision"] == "allow" for r in rows)
    assert json.loads(by_tool["dummy_agent"]["arguments"]) == {"input": "echo hi"}


def test_usage_includes_the_specialists_model_calls(tmp_path, dummy):
    runtime = make_runtime(tmp_path, list(NESTED))
    turn = asyncio.run(runtime.ask("please echo hi"))
    assert turn.usage.requests == 4
    assert [e.tool for e in turn.tool_events] == ["dummy_echo", "dummy_agent"]


def test_specialist_sees_the_current_access_level(tmp_path, dummy):
    runtime = make_runtime(tmp_path, list(NESTED))
    runtime.context.set_level("2.1")
    asyncio.run(runtime.ask("please echo hi"))
    assert "echo:hi level:2.1" in json.dumps(runtime.model.calls[2]["input"], default=str)


def test_blocked_inside_a_specialist_reaches_the_orchestrator(tmp_path, dummy):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret-ish")
    work = tmp_path / "work"
    work.mkdir()
    runtime = make_runtime(work, [
        tool_call("dummy_agent", {"input": f"read {outside}"}, call_id="c1"),
        tool_call("filesystem_read", {"path": str(outside)}, call_id="c2"),
        text("BLOCKED: outside the working folder."),
        text("The file is outside the working folder; use /level 2.1."),
    ])
    out = drive(runtime, ["read that file"])
    assert "[Dummy] <- filesystem_read: BLOCKED:" in out
    assert "secret-ish" not in json.dumps(runtime.model.calls, default=str)
    rows = {r["tool"]: r for r in runtime.audit.recent(10)}
    assert rows["filesystem_read"]["decision"] == "block"
    assert rows["filesystem_read"]["agent"] == "Dummy"


def test_specialist_turn_limit_comes_from_config(tmp_path, dummy):
    looping = [tool_call("dummy_agent", {"input": "loop"}, call_id="c1")]
    # The specialist keeps calling its tool; after 2 turns the nested run is stopped and
    # the next scripted output goes to the orchestrator again.
    looping += [tool_call("dummy_echo", {"text": "x"}, call_id=f"n{i}") for i in range(2)]
    looping += [text("The specialist ran out of turns.")]
    runtime = make_runtime(tmp_path, looping, "orchestration:\n  specialist_max_turns: 2\n")
    turn = asyncio.run(runtime.ask("loop"))
    echoes = [e for e in turn.tool_events if e.tool == "dummy_echo"]
    assert len(echoes) == 2  # the nested run stopped after 2 turns
    agent_event = [e for e in turn.tool_events if e.tool == "dummy_agent"][0]
    assert agent_event.result_preview.startswith("ERROR: specialist Dummy used all 2 max turns")
    assert "try again" not in agent_event.result_preview.lower()  # not the SDK default text
    assert turn.text == "The specialist ran out of turns."  # the chat goes on
    row = {r["tool"]: r for r in runtime.audit.recent(10)}["dummy_agent"]
    assert row["decision"] == "error" and "used all 2 max turns" in row["reason"]
