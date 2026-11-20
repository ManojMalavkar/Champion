"""Step M0: what parallel tracks need from main.

A demo track package (``m0demo``) is written into a temporary folder and added to
``champion.specialists``, exactly as a track adds ``champion/specialists/<track>/``.
Without editing any shared file it must be found, bring its own config section
and offer a group with two sub-agents that are shown, audited and stopped by a
"no" like every other agent. All with the scripted fake model, no network.
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from fake_model import FakeModel, text, tool_call

import champion.specialists as specialists_package
from champion.agents.orchestrator import DECLINED_ANSWER, orchestrator_instructions
from champion.chat import chat_loop
from champion.core.config import ConfigError, example_fragment_problems, load_config
from champion.core.runtime import ChampionRuntime
from champion.policy.tool_policy import ToolRule, register
from champion.specialists import registry
from champion.specialists.base import SpecialistSpec, all_tool_names
from champion.specialists.registry import (check_spec, register_specialist, select_specialists,
                                           specialist_modules, unregister_specialist)
from champion.tools.session_info import describe_session

DEMO_PACKAGE = '''
from agents import RunContextWrapper, function_tool

from champion.core.context import ChampionContext
from champion.policy.tool_policy import ToolRule, register
from champion.specialists.base import SpecialistSpec
from champion.specialists.registry import register_specialist
from champion.tools.guard import run_guarded

register(ToolRule("m0demo_echo", "none", "Test tool of the M0 demo track"))
register(ToolRule("m0demo_decline", "none", "Test tool: the user says no"))


@function_tool
async def m0demo_echo(ctx: RunContextWrapper[ChampionContext], text: str) -> str:
    """Echo the text with the configured greeting."""
    greeting = ctx.context.config.get("m0demo.greeting")
    return await run_guarded(ctx, "m0demo_echo", {"text": text}, lambda: f"{greeting}:{text}")


@function_tool
async def m0demo_decline(ctx: RunContextWrapper[ChampionContext]) -> str:
    """Act as if the user answered no at the approval prompt."""
    ctx.context.declined.add("m0demo")
    return "BLOCKED: the user did not approve this (answered no at the approval prompt)."


def validate(section):
    return [] if isinstance(section.get("greeting"), str) else ["greeting must be text"]


ALPHA = SpecialistSpec(name="m0demo_alpha", title="Alpha", description="Echoes text.",
                       instructions="You are Alpha. Use m0demo_echo.", tools=(m0demo_echo,),
                       routing_hints=("echo with alpha",))
BETA = SpecialistSpec(name="m0demo_beta", title="Beta", description="Asks the user, who says no.",
                      instructions="You are Beta.", tools=(m0demo_decline,))
GROUP = register_specialist(SpecialistSpec(
    name="m0demo", title="M0 Demo", description="Demo group for the M0 tests.",
    instructions="You lead the demo group.", members=(ALPHA, BETA),
    config_section="m0demo", config_defaults={"greeting": "hi", "data_dir": "data"},
    config_validate=validate, config_path_keys=("data_dir",)))
'''

LINUX_OFF = "agents:\n  linux_coder:\n    enabled: false\n  coder:\n    enabled: false\n"


def add_package(folder: Path, name: str, source: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "__init__.py").write_text(source)


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    """A folder that counts as part of champion/specialists/ (like a track's package)."""
    folder = tmp_path / "plugins"
    folder.mkdir()
    monkeypatch.setattr(specialists_package, "__path__", [*specialists_package.__path__, str(folder)])
    importlib.invalidate_caches()
    yield folder
    for name in ("m0demo", "m0broken", "m0llm"):
        unregister_specialist(name)
        sys.modules.pop(f"champion.specialists.{name}", None)
        registry._LOAD_PROBLEMS.pop(name, None)


@pytest.fixture
def demo(plugins):
    add_package(plugins, "m0demo", DEMO_PACKAGE)
    return plugins


def make_config(tmp_path: Path, extra: str = ""):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: test-model\n" + extra)
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path)


def drive(runtime, lines):
    queue, out = list(lines), []

    def read_line(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    asyncio.run(chat_loop(runtime, read_line, out.append))
    return "\n".join(out)


# ------------------------------------------------------------ 1. discovery

def test_specialists_are_found_not_listed(tmp_path):
    source = Path(registry.__file__).read_text()
    assert "champion.specialists.linux" not in source and "champion.specialists.coder" not in source
    assert {"coder", "linux"} <= set(specialist_modules())
    assert not {"base", "registry"} & set(specialist_modules())
    selected, problems = select_specialists(make_config(tmp_path))
    assert [s.name for s in selected] == ["coder", "linux_coder"] and problems == []


def test_a_track_package_is_found_without_editing_shared_files(tmp_path, demo):
    selected, problems = select_specialists(make_config(tmp_path))
    assert problems == []
    group = next(s for s in selected if s.name == "m0demo")
    assert [m.name for m in group.members] == ["m0demo_alpha", "m0demo_beta"]
    assert "m0demo_echo" in all_tool_names(group)


def test_a_broken_track_is_reported_and_champion_still_starts(tmp_path, plugins):
    add_package(plugins, "m0broken", "raise RuntimeError('boom')\n")
    selected, problems = select_specialists(make_config(tmp_path))
    assert [s.name for s in selected] == ["coder", "linux_coder"]
    assert problems == ["specialist module m0broken could not be loaded (RuntimeError: boom)"]


# -------------------------------------------------- 2. config of an agent

def test_the_track_brings_its_own_config_section(tmp_path, demo):
    config = make_config(tmp_path)
    assert config.get("m0demo.greeting") == "hi"
    assert config.get("m0demo.data_dir") == str((tmp_path / "data").resolve())  # path key: project folder
    config = make_config(tmp_path, "m0demo:\n  greeting: hello\n  colour: red\n")
    assert config.get("m0demo.greeting") == "hello"
    assert "Unknown config key 'm0demo.colour' (ignored; check spelling)" in config.warnings
    with pytest.raises(ConfigError, match="m0demo: greeting must be text"):
        make_config(tmp_path, "m0demo:\n  greeting: 5\n")


def test_a_track_cannot_take_a_built_in_section(tmp_path, plugins):
    source = DEMO_PACKAGE.replace('name="m0demo", title', 'name="m0llm", title').replace(
        'config_section="m0demo"', 'config_section="llm"')
    add_package(plugins, "m0llm", source)
    selected, problems = select_specialists(make_config(tmp_path))
    assert "m0llm" not in [s.name for s in selected]
    assert problems == ["specialist module m0llm could not be loaded (ValueError: config section llm is a "
                        "built-in section of Champion)"]


def test_every_agent_section_has_its_example_file(tmp_path, demo):
    assert example_fragment_problems(tmp_path / "none") == []  # before loading: no agent sections
    make_config(tmp_path)  # loads the demo track
    examples = tmp_path / "examples"
    examples.mkdir()
    assert example_fragment_problems(examples) == ["m0demo: example file m0demo.yaml is missing"]
    (examples / "m0demo.yaml").write_text("m0demo:\n  greeting: hi\n")
    assert example_fragment_problems(examples) == ["m0demo: m0demo.yaml must contain exactly the "
                                                   "section's defaults"]
    (examples / "m0demo.yaml").write_text("m0demo:\n  greeting: hi\n  data_dir: data\n")
    (examples / "old.yaml").write_text("old: {}\n")
    assert example_fragment_problems(examples) == ["old.yaml: no agent declares a config section old"]


def test_the_repository_examples_match_the_agents(tmp_path):
    make_config(tmp_path)  # loads the agents of this repository
    assert example_fragment_problems() == []


# ------------------------------------------------------ 3. sub-agents

register(ToolRule("m0_rule_echo", "none", "Test tool for spec checks"))


def spec(name, **changes):
    base = dict(name=name, title=name.title(), description="d", instructions="i")
    return SpecialistSpec(**{**base, **changes})


@pytest.mark.parametrize("group, problem", [
    (spec("grp", members=(spec("sub", members=(spec("subsub"),)),)),
     "sub-agent sub: groups are at most 2 levels deep"),
    (spec("grp", members=(spec("sub"), spec("sub"))), "sub-agent name sub is used twice"),
    (spec("grp", members=(spec("sub", config_section="sub"),)),
     "sub-agent sub: a sub-agent cannot declare a config section"),
    (spec("grp", members=(spec("Bad-Name"),)), "sub-agent Bad-Name: name must be"),
])
def test_group_problems(group, problem):
    assert any(problem in p for p in check_spec(group)), check_spec(group)


def test_names_must_be_unique_across_groups(tmp_path):
    first = spec("grp", members=(spec("shared"),))
    second = spec("shared")
    selected, problems = select_specialists(make_config(tmp_path), [first, second])
    assert selected == [first]
    assert problems == ["specialist shared not offered: name shared is already used by another agent"]


def test_sub_agents_are_switched_in_the_config(tmp_path, demo):
    config = make_config(tmp_path, "agents:\n  m0demo_beta:\n    enabled: false\n")
    group = next(s for s in select_specialists(config)[0] if s.name == "m0demo")
    assert [m.name for m in group.members] == ["m0demo_alpha"]
    config = make_config(tmp_path, "agents:\n  m0demo_alpha:\n    enabled: false\n"
                                   "  m0demo_beta:\n    enabled: false\n")
    assert "m0demo" not in [s.name for s in select_specialists(config)[0]]


GROUP_RUN = [
    tool_call("m0demo_agent", {"input": "echo hi"}, call_id="c1"),  # orchestrator -> group
    tool_call("m0demo_alpha_agent", {"input": "echo hi"}, call_id="c2"),  # group -> sub-agent
    tool_call("m0demo_echo", {"text": "hi"}, call_id="c3"),  # sub-agent -> its tool
    text("Alpha echoed hi."),  # sub-agent's answer
    text("Alpha says hi."),  # group's answer
    text("The demo group says hi."),  # orchestrator's reply
]


def test_a_group_runs_its_sub_agents_shown_and_audited(tmp_path, demo):
    runtime = ChampionRuntime(make_config(tmp_path, LINUX_OFF), model=FakeModel(list(GROUP_RUN)))
    out = drive(runtime, ["please echo hi"])
    calls = runtime.model.calls
    assert len(calls) == 6
    # The orchestrator sees only the group, with its sub-agents named in the instructions.
    assert calls[0]["tools"][-1] == "m0demo_agent" and "m0demo_alpha_agent" not in calls[0]["tools"]
    assert "It leads: Alpha, Beta" in calls[0]["instructions"]
    assert '"echo with alpha"' in calls[0]["instructions"]
    # The group agent gets its sub-agents as tools; each sub-agent only its own tools.
    assert calls[1]["tools"] == ["m0demo_alpha_agent", "m0demo_beta_agent"]
    assert "You lead a group of sub-agents" in calls[1]["instructions"]
    assert "- m0demo_alpha_agent (Alpha): Echoes text." in calls[1]["instructions"]
    assert calls[2]["tools"] == ["m0demo_echo"]
    assert "Never try to get around a block" in calls[2]["instructions"]
    assert "hi:hi" in json.dumps(calls[3]["input"], default=str)  # config value reached the tool
    # Banner, tool lines one step deeper per level, final reply.
    assert "specialists: m0demo_agent (m0demo_alpha_agent, m0demo_beta_agent)" in out
    assert '  -> m0demo_agent({"input": "echo hi"})' in out
    assert '      [M0 Demo] -> m0demo_alpha_agent({"input": "echo hi"})' in out
    assert '          [M0 Demo/Alpha] -> m0demo_echo({"text": "hi"})' in out
    assert "champion> The demo group says hi." in out
    # Audit: one row per call, each with the agent that made it.
    agents = {r["tool"]: r["agent"] for r in runtime.audit.recent(10)}
    assert agents == {"m0demo_agent": "Champion", "m0demo_alpha_agent": "M0 Demo",
                      "m0demo_echo": "M0 Demo/Alpha"}
    assert describe_session(runtime.context)["sub_agents"] == {
        "m0demo_agent": ["m0demo_alpha_agent", "m0demo_beta_agent"]}


def test_a_no_inside_a_sub_agent_ends_the_whole_turn(tmp_path, demo):
    outputs = [tool_call("m0demo_agent", {"input": "do it"}, call_id="c1"),
               tool_call("m0demo_beta_agent", {"input": "do it"}, call_id="c2"),
               tool_call("m0demo_decline", {}, call_id="c3")]  # no model call is scripted after this
    runtime = ChampionRuntime(make_config(tmp_path, LINUX_OFF), model=FakeModel(outputs))
    out = drive(runtime, ["do it"])
    assert len(runtime.model.calls) == 3  # sub-agent, group and orchestrator are not asked again
    assert f"champion> {DECLINED_ANSWER}" in out


def test_routing_rule_for_checks_also_finds_tools_of_sub_agents():
    from champion.tools.checks import run_check

    group = spec("grp", members=(spec("runner", tools=(run_check,)),))
    assert "Running tests or other checks: always grp_agent" in orchestrator_instructions([group])
    assert "Running tests or other checks" not in orchestrator_instructions([spec("plain")])


def test_without_groups_nothing_changes(tmp_path):
    runtime = ChampionRuntime(make_config(tmp_path), model=FakeModel([text("Hi.")]))
    assert runtime.context.sub_agents == {}
    assert runtime.context.specialists == ["coder_agent", "linux_coder_agent"]
