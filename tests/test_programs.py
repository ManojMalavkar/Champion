"""M2: program tools (trusted external programs and scripts). No network, no model."""

import asyncio
import json
import os
import stat

import pytest
import yaml
from agents import RunContextWrapper

from champion.cli import main
from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.doctor import WARN, check_programs
from champion.policy.scope import PolicyDenied
from champion.programs import manifest as pm
from champion.programs.runner import run_program
from champion.programs.spec import SpecError, parse_entry, register_program, unregister_program
from champion.programs.tools import LIST_TOOL, RUN_TOOL, attach_program_tools
from champion.specialists.linux import LINUX as LINUX_CODER
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError

ECHO = "#!/bin/sh\nfor a in \"$@\"; do echo \"arg=$a\"; done\necho \"foo=${FOO:-unset} bar=${BAR:-unset}\"\n"


def entry(name="echo_args", **extra):
    data = {"name": name, "program": "echo.sh", "description": "prints its arguments", "agent": "linux_coder",
            "class": "read", "args": ["--x", "{word}"],
            "params": {"word": {"type": "string", "required": True}}}
    data.update(extra)
    return data


def setup(tmp_path, entries, scripts=None, extra=None):
    work = (tmp_path / "work").resolve()
    work.mkdir(exist_ok=True)
    folder = (tmp_path / "progs").resolve()
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for name, body in (scripts or {"echo.sh": ECHO}).items():
        (folder / name).write_text(body)
        os.chmod(folder / name, 0o755)
    (folder / "programs.yaml").write_text(yaml.safe_dump({"programs": entries}))
    os.chmod(folder / "programs.yaml", 0o600)
    data = {"llm": {"model_id": "m"}, "execution": {"working_directory": str(work)},
            "programs": {"folder": str(folder), "trust_file": str(tmp_path / "trust" / "programs.trust")},
            **(extra or {})}
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(yaml.safe_dump(data))
    config = load_config(tmp_path / "config" / "config.yaml", env={}, cwd=tmp_path)
    return config, work, folder


def ctx(config, level="1.2", approver=None):
    context = ChampionContext.from_config(config, level=level, audit=AuditLog())
    context.approver = approver
    return context


def spec_of(config, work, name="echo_args"):
    return next(s.spec for s in pm.program_states(config, work) if s.name == name)


# ------------------------------------------------------------------ declaration

@pytest.mark.parametrize("change, expected", [
    ({"name": "Bad-Name"}, "name must be"),
    ({"colour": "red"}, "unknown key"),
    ({"class": "run"}, "class must be one of"),
    ({"args": ["{other}"]}, "names no parameter"),
    ({"args": ["--w={word}"]}, "whole token"),
    ({"env_pass": ["API_TOKEN"]}, "looks like a secret"),
    ({"env_set": {"PATH": "/opt/bin"}}, "set by Champion"),
    ({"env_set": {"LD_PRELOAD": "/x.so"}}, "set by Champion"),
    ({"params": {"word": {"type": "path", "access": "edit", "required": True}}}, "read program cannot"),
    ({"params": {"word": {"type": "bool"}}}, "flag must be"),
    ({"params": {"word": {"type": "int", "min": 5, "max": 1}}}, "min is larger"),
    ({"params": {"word": {"type": "string"}, "unused": {"type": "int"}}}, "unused is not used"),
    ({"timeout_seconds": 0}, "timeout_seconds"),
    ({"approval": "never"}, "approval must be"),
    ({"path_prepend": ["relative/bin"]}, "absolute folder"),
])
def test_bad_entries_are_refused_with_reasons(change, expected):
    with pytest.raises(SpecError) as error:
        parse_entry(entry(**change))
    assert any(expected in problem for problem in error.value.problems), error.value.problems


def test_good_entry_schema_and_rights():
    spec = parse_entry(entry(**{"class": "delete", "params": {
        "word": {"type": "path", "access": "edit", "required": True},
        "mode": {"type": "enum", "values": ["fast", "full"], "default": "fast"}}, "args": ["{word}", "{mode}"]}))
    assert spec.tool_name == "program_echo_args" and spec.right == "edit" and spec.asks
    schema = spec.schema()
    assert schema["required"] == ["word", "mode"] and schema["additionalProperties"] is False
    assert schema["properties"]["mode"]["enum"] == ["fast", "full", None]
    assert parse_entry(entry()).right == "read" and not parse_entry(entry()).asks
    assert parse_entry(entry(approval="always")).asks
    remote = parse_entry(entry(**{"class": "remote"}))  # decision 2: remote is not local edit access
    assert remote.asks and remote.right == "read"


@pytest.mark.parametrize("cls", ["edit", "delete"])
def test_edit_and_delete_must_declare_their_paths(cls):
    with pytest.raises(SpecError, match="must declare the paths it changes"):
        parse_entry(entry(**{"class": cls}))


BASE = entry(params={"word": {"type": "string", "required": True},
                     "dir": {"type": "path", "access": "read", "required": True}},
             args=["--x", "{word}", "{dir}"])


@pytest.mark.parametrize("field, value", [
    ("program", "other.sh"), ("class", "remote"), ("agent", "coder"), ("description", "something else"),
    ("args", ["--y", "{word}", "{dir}"]),
    ("params", {"word": {"type": "string", "required": True, "pattern": "[a-z]+"},
                "dir": {"type": "path", "access": "read", "required": True}}),
    ("params", {"word": {"type": "enum", "values": ["a"], "required": True},
                "dir": {"type": "path", "access": "read", "required": True}}),
    ("cwd", "{dir}"), ("env_pass", ["FOO"]), ("env_set", {"LIC": "1@srv"}), ("path_prepend", ["/opt/x/bin"]),
    ("interpreter", "/usr/bin/python3"), ("timeout_seconds", 301), ("max_memory_bytes", 1024),
    ("max_file_bytes", 1024), ("approval", "always"),
])
def test_entry_digest_covers_every_field(field, value):
    base = parse_entry(BASE)
    changed = parse_entry({**BASE, field: value})
    assert changed.fingerprint != base.fingerprint
    assert parse_entry(dict(BASE)).fingerprint == base.fingerprint  # stable for the same entry
    assert parse_entry(BASE, source="track:demo").fingerprint != base.fingerprint


# ------------------------------------------------------------------ trust and status

def test_untrusted_then_trusted_then_changed(tmp_path):
    config, work, folder = setup(tmp_path, [entry()])
    assert [s.status for s in pm.program_states(config, work)] == [pm.UNTRUSTED]
    state = pm.trust(config, "echo_args", work)
    assert state.status == pm.TRUSTED and state.argv0 == ("/bin/sh", str(folder / "echo.sh"))
    trust_file = tmp_path / "trust" / "programs.trust"
    assert stat.S_IMODE(os.stat(trust_file).st_mode) == 0o600
    (folder / "echo.sh").write_text(ECHO + "echo extra\n")
    changed = pm.program_states(config, work)[0]
    assert changed.status == pm.CHANGED and "program file" in changed.detail
    with pytest.raises(PolicyDenied, match="changed"):
        run_program(ctx(config), changed.spec, {"word": "a"})
    pm.trust(config, "echo_args", work)
    assert pm.untrust(config, "echo_args") and pm.program_states(config, work)[0].status == pm.UNTRUSTED


def test_entry_change_needs_new_trust(tmp_path):
    config, work, _ = setup(tmp_path, [entry()])
    pm.trust(config, "echo_args", work)
    config, work, _ = setup(tmp_path, [entry(args=["--y", "{word}"])])
    state = pm.program_states(config, work)[0]
    assert state.status == pm.CHANGED and "entry" in state.detail


@pytest.mark.parametrize("problem", ["group_writable", "no_shebang", "foreign_interpreter", "in_working"])
def test_unsafe_program_files_are_invalid(tmp_path, problem):
    scripts = {"echo.sh": ECHO}
    if problem == "no_shebang":
        scripts = {"echo.sh": "echo hi\n"}
    if problem == "foreign_interpreter":
        scripts = {"echo.sh": "#!/opt/odd/python\nprint(1)\n"}
    config, work, folder = setup(tmp_path, [entry()], scripts)
    if problem == "group_writable":
        os.chmod(folder / "echo.sh", 0o775)
    if problem == "in_working":
        (work / "echo.sh").write_text(ECHO)
        config, work, folder = setup(tmp_path, [entry(program=str(work / "echo.sh"))])
    state = pm.program_states(config, work)[0]
    assert state.status == pm.INVALID, state
    with pytest.raises(pm.ProgramError):
        pm.trust(config, "echo_args", work)


def test_unsafe_manifest_folder_and_duplicates(tmp_path):
    config, work, folder = setup(tmp_path, [entry(), entry()])
    assert any(s.status == pm.INVALID and "two entries" in s.detail for s in pm.program_states(config, work))
    os.chmod(folder, 0o777)
    states = pm.program_states(config, work)
    assert states[0].name == pm.MANIFEST and "group or others" in states[0].detail


def test_env_shebang_and_declared_interpreter_is_pinned(tmp_path):
    scripts = {"echo.sh": "#!/usr/bin/env sh\necho via-env\n", "tool.py": "print('py')\n"}
    config, work, folder = setup(tmp_path, [entry(), entry("py_tool", program="tool.py", interpreter=os.path.realpath("/bin/sh"),
                                                            args=[], params={})], scripts)
    states = {s.name: s for s in pm.program_states(config, work)}
    echo = states["echo_args"]
    assert echo.argv0[0] in ("/usr/bin/sh", "/bin/sh") and echo.interpreter_requested == echo.argv0[0]
    # decision 3: the system interpreter is hashed too (the real file behind the links)
    assert echo.interpreter_resolved == os.path.realpath(echo.argv0[0])
    assert echo.interpreter_sha256 == pm.sha256_file(echo.interpreter_resolved)
    assert states["py_tool"].interpreter_sha256 == pm.sha256_file(os.path.realpath("/bin/sh"))


# ------------------------------------------------------------------ running

def trusted(tmp_path, entries, scripts=None, extra=None):
    config, work, folder = setup(tmp_path, entries, scripts, extra)
    for item in entries:
        pm.trust(config, item["name"], work)
    return config, work, folder


def test_read_program_runs_without_shell(tmp_path):
    config, work, _ = trusted(tmp_path, [entry(params={"word": {"type": "string", "required": True,
                                                                "pattern": r"[A-Za-z;$ ]+"}})])
    audit = {}
    out = run_program(ctx(config, level="1.1"), spec_of(config, work), {"word": "a; echo $HOME"}, audit)
    assert "arg=--x" in out and "arg=a; echo $HOME" in out and "exit code 0" in out
    assert audit["exit_code"] == 0 and len(audit["sha256"]) == 64 and audit["class"] == "read"


@pytest.mark.parametrize("value, expected", [("-rf", "cannot start with '-'"), ("a b", "does not match"),
                                             ("x\ny", "control character"), ("", "1-200"), ("a" * 201, "1-200")])
def test_string_values_are_checked(tmp_path, value, expected):
    config, work, _ = trusted(tmp_path, [entry()])
    with pytest.raises(ToolError, match=expected):
        run_program(ctx(config), spec_of(config, work), {"word": value})


def test_unknown_and_missing_arguments(tmp_path):
    config, work, _ = trusted(tmp_path, [entry()])
    with pytest.raises(ToolError, match="unknown argument"):
        run_program(ctx(config), spec_of(config, work), {"word": "a", "extra": 1})
    with pytest.raises(ToolError, match="required"):
        run_program(ctx(config), spec_of(config, work), {})


def test_typed_values_and_optional_tokens(tmp_path):
    params = {"n": {"type": "int", "min": 1, "max": 8}, "keep": {"type": "bool", "flag": "--keep"},
              "mode": {"type": "enum", "values": ["fast", "full"], "default": "fast"}}
    config, work, _ = trusted(tmp_path, [entry(args=["{n}", "{keep}", "{mode}"], params=params)])
    spec = spec_of(config, work)
    out = run_program(ctx(config), spec, {"n": 4, "keep": True})
    assert "arg=4\narg=--keep\narg=fast" in out
    out = run_program(ctx(config), spec, {"keep": False, "mode": "full"})
    assert "arg=full" in out and "arg=--keep" not in out and "arg=4" not in out
    for bad in ({"n": 9}, {"n": "4"}, {"n": True}, {"mode": "slow"}, {"keep": "yes"}):
        with pytest.raises(ToolError):
            run_program(ctx(config), spec, bad)


def test_paths_are_checked_like_file_tools(tmp_path):
    params = {"word": {"type": "path", "access": "read", "required": True}}
    config, work, _ = trusted(tmp_path, [entry(params=params)])
    (work / "deck.k").write_text("*END\n")
    out = run_program(ctx(config), spec_of(config, work), {"word": "deck.k"})
    assert f"arg={work / 'deck.k'}" in out
    for bad in (".champion/backups/x", "/etc/shadow"):
        with pytest.raises(PolicyDenied):
            run_program(ctx(config), spec_of(config, work), {"word": bad})
    os.symlink(work / "deck.k", work / "link.k")
    with pytest.raises(PolicyDenied, match="symbolic"):
        run_program(ctx(config), spec_of(config, work), {"word": "link.k"})


def test_edit_program_needs_edit_level_and_is_listed(tmp_path):
    params = {"word": {"type": "path", "access": "edit", "required": True}}
    config, work, _ = trusted(tmp_path, [entry(**{"class": "edit", "params": params})])
    with pytest.raises(PolicyDenied, match="does not allow edit"):
        run_program(ctx(config, level="1.1"), spec_of(config, work), {"word": "out"})
    context = ctx(config, level="1.2")
    run_program(context, spec_of(config, work), {"word": "out"}, agent="linux_coder")
    [change] = context.audit.session_changes()
    assert change["action"] == "modified" and change["path"] == str(work / "out") and change["undo"] is None


def test_delete_and_remote_ask_every_time(tmp_path):
    params = {"word": {"type": "path", "access": "edit", "required": True}}
    config, work, folder = trusted(tmp_path, [entry(**{"class": "delete", "params": params}),
                                              entry("submit", **{"class": "remote"})])
    with pytest.raises(PolicyDenied, match="non-interactive"):
        run_program(ctx(config), spec_of(config, work), {"word": "run1"})
    asked = []
    context = ctx(config, approver=lambda request: asked.append(request) or True)
    run_program(context, spec_of(config, work), {"word": "run1"})
    run_program(context, spec_of(config, work, "submit"), {"word": "job1"})
    assert [r.command_class for r in asked] == ["delete", "remote"]
    sha = pm.sha256_file(str(folder / "echo.sh"))
    assert sha[:16] in asked[0].note and asked[0].paths == (str(work / "run1"),)
    assert "Champion cannot undo" in asked[1].describe()
    assert [c["action"] for c in context.audit.session_changes()] == ["deleted"]
    no = ctx(config, approver=lambda request: False)
    with pytest.raises(PolicyDenied):
        run_program(no, spec_of(config, work, "submit"), {"word": "job1"})


def test_program_changed_during_approval_is_not_run(tmp_path):
    config, work, folder = trusted(tmp_path, [entry(approval="always")])

    def approver(request):
        (folder / "echo.sh").write_text(ECHO + "echo evil\n")
        return True

    with pytest.raises(PolicyDenied, match="changed just before"):
        run_program(ctx(config, approver=approver), spec_of(config, work), {"word": "a"})


def test_environment_only_declared_names(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO", "from-user")
    monkeypatch.setenv("BAR", "not-listed")
    config, work, _ = trusted(tmp_path, [entry(env_pass=["FOO", "MISSING_ONE"], env_set={"LIC": "1234@srv"},
                                               path_prepend=["/opt/tools/bin"])])
    audit = {}
    out = run_program(ctx(config), spec_of(config, work), {"word": "a"}, audit)
    assert "foo=from-user bar=unset" in out and "MISSING_ONE" in out
    assert audit["env"] == ["FOO", "LIC"]


def test_timeout_kills_the_program(tmp_path):
    config, work, _ = trusted(tmp_path, [entry("slow", program="slow.sh", args=[], params={}, timeout_seconds=1)],
                              {"slow.sh": "#!/bin/sh\nsleep 30\n"})
    out = run_program(ctx(config), spec_of(config, work, "slow"), {})
    assert "time limit of 1 s" in out


def test_cwd_parameter_runs_in_that_folder(tmp_path):
    params = {"run_dir": {"type": "path", "access": "read", "required": True}}
    config, work, _ = trusted(tmp_path, [entry("where", program="pwd.sh", args=[], params=params, cwd="{run_dir}")],
                              {"pwd.sh": "#!/bin/sh\npwd\n"})
    (work / "r1").mkdir()
    assert str(work / "r1") in run_program(ctx(config), spec_of(config, work, "where"), {"run_dir": "r1"})


def test_timeout_above_config_maximum_is_invalid(tmp_path):
    config, work, _ = setup(tmp_path, [entry(timeout_seconds=7200)])
    state = pm.program_states(config, work)[0]
    assert state.status == pm.INVALID and "max_timeout_seconds" in state.detail


# ------------------------------------------------------------------ tools for the agents

def test_attach_adds_trusted_programs_to_their_agent(tmp_path):
    config, work, _ = setup(tmp_path, [entry(), entry("untrusted_one"), entry("for_nobody", agent="nobody")])
    pm.trust(config, "echo_args", work)
    pm.trust(config, "for_nobody", work)
    specs, problems = attach_program_tools(config, [LINUX_CODER], work)
    assert "program_echo_args" in specs[0].tool_names and "program_untrusted_one" not in specs[0].tool_names
    assert any("untrusted_one not offered: untrusted" in p for p in problems)
    assert any("for_nobody not offered: agent nobody" in p for p in problems)
    tool = next(t for t in specs[0].tools if t.name == "program_echo_args")
    out = asyncio.run(tool.on_invoke_tool(RunContextWrapper(ctx(config)), json.dumps({"word": "hello"})))
    assert "arg=hello" in out


def test_orchestrator_and_start_line_know_the_programs(tmp_path):
    """Linux check 25 Nov 2026: the orchestrator answered itself; the agent description never named the programs."""
    from fake_model import FakeModel

    from champion.agents.orchestrator import orchestrator_instructions
    from champion.chat import chat_loop
    from champion.core.runtime import ChampionRuntime

    config, work, _ = trusted(tmp_path, [entry()])
    runtime = ChampionRuntime(config, model=FakeModel([]), audit=AuditLog(), level="1.2")
    assert runtime.program_tools == ["program_echo_args (linux_coder)"]
    linux = next(spec for spec in runtime.specialists if spec.name == "linux_coder")
    assert "program_echo_args" in linux.tool_names
    assert "send it these tasks: program_echo_args: prints its arguments" in linux.description
    # Linux check: the agent asked "shall I go ahead?" in text instead of calling the tool
    assert "Do not ask the user for confirmation in your answer first" in linux.instructions
    assert linux.instructions.startswith(LINUX_CODER.instructions.rstrip())
    assert "program_echo_args: prints its arguments" in orchestrator_instructions(runtime.specialists)
    out = []
    asyncio.run(chat_loop(runtime, lambda prompt: "/quit", out.append))
    assert "Programs: program_echo_args (linux_coder)." in out


def test_orchestrator_does_not_refuse_from_its_own_reading(tmp_path):
    """Linux check: the orchestrator refused a clean request itself, naming a wrong working folder."""
    import types

    from champion.agents.orchestrator import orchestrator_instructions, with_current_level

    config, work, _ = trusted(tmp_path, [entry()])
    text = orchestrator_instructions([LINUX_CODER])
    assert "Never refuse a work request from your own reading of the access list" in text
    # Linux check: after a decline it asked "reply yes to proceed" in text and sent "yes" to the agent
    assert 'Never ask the user to confirm in text ("reply yes to proceed")' in text
    context = ctx(config)
    text = with_current_level("Base.")(RunContextWrapper(context), types.SimpleNamespace(name="x"))
    assert f"Working folder: {context.working_directory} (relative paths start here)." in text


def test_many_programs_use_list_and_run(tmp_path):
    entries = [entry(), entry("second")]
    config, work, _ = trusted(tmp_path, entries, extra={"programs": {
        "folder": str((tmp_path / "progs").resolve()), "trust_file": str(tmp_path / "trust" / "programs.trust"),
        "max_direct_tools": 1}})
    specs, _ = attach_program_tools(config, [LINUX_CODER], work)
    names = specs[0].tool_names
    assert LIST_TOOL in names and RUN_TOOL in names and "program_second" not in names
    run = next(t for t in specs[0].tools if t.name == RUN_TOOL)
    wrapper = RunContextWrapper(ctx(config))
    out = asyncio.run(run.on_invoke_tool(wrapper, json.dumps({"name": "second", "arguments": '{"word": "b"}'})))
    assert "arg=b" in out
    out = asyncio.run(run.on_invoke_tool(wrapper, json.dumps({"name": "nope", "arguments": "{}"})))
    assert "no trusted program" in out


def test_track_program_needs_trust_too(tmp_path):
    config, work, folder = setup(tmp_path, [])
    register_program(entry("track_tool", program=str(folder / "echo.sh")), package="demo")
    try:
        [state] = pm.program_states(config, work)
        assert state.status == pm.UNTRUSTED and state.source == "track:demo"
        pm.trust(config, "track_tool", work)
        assert pm.program_states(config, work)[0].status == pm.TRUSTED
    finally:
        unregister_program("track_tool")


# ------------------------------------------------------------------ CLI and doctor

def test_cli_list_trust_check(tmp_path, capsys):
    config, work, _ = setup(tmp_path, [entry(), entry("broken", program="missing.sh")])
    path = str(tmp_path / "config" / "config.yaml")
    assert main(["--config", path, "programs", "list"]) == 0
    assert main(["--config", path, "programs", "check"]) == 1  # broken is missing
    assert main(["--config", path, "programs", "trust", "echo_args"]) == 0
    assert main(["--config", path, "programs", "trust", "broken"]) == 1
    assert main(["--config", path, "programs", "trust"]) == 2
    out = capsys.readouterr()
    assert "untrusted" in out.out and "echo_args: trusted" in out.out and "SHA-256" in out.out
    assert "missing" in out.out
    lines = check_programs(config)
    assert {line.name: line.status for line in lines}["Program broken"] == WARN


# ------------------------------------------------------------------ M2 review (25 Nov 2026): decisions 1-5

def copy_interpreter(folder):
    """A user-owned copy of the system sh (a real interpreter the test may change)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "sh"
    target.write_bytes(open(os.path.realpath("/bin/sh"), "rb").read())
    os.chmod(target, 0o755)
    return target


def test_remote_needs_approval_but_not_local_edit_access(tmp_path):
    edit_param = {"word": {"type": "string", "required": True}, "out": {"type": "path", "access": "edit"}}
    config, work, _ = trusted(tmp_path, [entry("submit", **{"class": "remote"}),
                                         entry("fetch", **{"class": "remote", "params": edit_param,
                                                           "args": ["{word}", "{out}"]})])
    asked = []
    read_only = ctx(config, level="1.1", approver=lambda r: asked.append(r) or True)
    out = run_program(read_only, spec_of(config, work, "submit"), {"word": "job1"})
    assert "arg=job1" in out and "remote actions are audited but cannot be undone" in out
    assert [r.command_class for r in asked] == ["remote"]
    with pytest.raises(PolicyDenied, match="changes the paths given as out"):
        run_program(read_only, spec_of(config, work, "fetch"), {"word": "job1", "out": "result.txt"})
    with pytest.raises(PolicyDenied, match="non-interactive"):
        run_program(ctx(config, level="1.2"), spec_of(config, work, "submit"), {"word": "job1"})


def test_edit_results_warn_that_there_is_no_undo(tmp_path):
    params = {"word": {"type": "path", "access": "edit", "required": True}}
    config, work, _ = trusted(tmp_path, [entry(**{"class": "edit", "params": params})])
    context = ctx(config)
    out = run_program(context, spec_of(config, work), {"word": "out"})
    assert "cannot be undone (undo of program changes needs the staging of M3)" in out
    assert "no undo (M3 staging)" in context.audit.session_changes()[0]["detail"]


def test_linked_interpreter_pins_requested_and_resolved(tmp_path):
    real = copy_interpreter(tmp_path / "real")
    other = copy_interpreter(tmp_path / "other")
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    os.symlink(real, venv / "python")  # like a venv python: a link to the real interpreter
    config, work, _ = setup(tmp_path, [entry(interpreter=str(venv / "python"))])
    state = pm.trust(config, "echo_args", work)
    assert (state.interpreter_requested, state.interpreter_resolved) == (str(venv / "python"), str(real))
    pinned = json.loads((tmp_path / "trust" / "programs.trust").read_text())["programs"]["echo_args"]
    assert pinned["interpreter_requested"] == str(venv / "python") and pinned["interpreter_resolved"] == str(real)
    assert "arg=a" in run_program(ctx(config), spec_of(config, work), {"word": "a"})
    os.unlink(venv / "python")
    os.symlink(other, venv / "python")  # same bytes, other file: still a change
    moved = pm.program_states(config, work)[0]
    assert moved.status == pm.CHANGED and "interpreter target" in moved.detail
    os.unlink(venv / "python")
    os.symlink(real, venv / "python")
    assert pm.program_states(config, work)[0].status == pm.TRUSTED
    with open(real, "ab") as stream:
        stream.write(b"\0")
    changed = pm.program_states(config, work)[0]
    assert changed.status == pm.CHANGED and "interpreter" in changed.detail


def test_env_shebang_uses_path_prepend_and_pins_it(tmp_path):
    tools = tmp_path / "tools" / "bin"
    mysh = copy_interpreter(tools)
    os.rename(mysh, tools / "mysh")
    config, work, _ = trusted(tmp_path, [entry(path_prepend=[str(tools)])],
                              {"echo.sh": "#!/usr/bin/env mysh\necho via-prepend \"$@\"\n"})
    state = pm.program_states(config, work)[0]
    assert state.interpreter_requested == str(tools / "mysh") and state.interpreter_sha256
    assert "via-prepend --x a" in run_program(ctx(config), spec_of(config, work), {"word": "a"})
    os.chmod(tools / "mysh", 0o777)
    assert pm.program_states(config, work)[0].status == pm.INVALID


def test_interpreter_inside_the_working_folder_is_refused(tmp_path):
    work = (tmp_path / "work").resolve()
    inside = copy_interpreter(work / "bin")
    config, work, _ = setup(tmp_path, [entry(interpreter=str(inside))])
    state = pm.program_states(config, work)[0]
    assert state.status == pm.INVALID and "inside the working folder" in state.detail


def test_interpreter_changed_during_approval_is_not_run(tmp_path):
    real = copy_interpreter(tmp_path / "real")
    config, work, _ = trusted(tmp_path, [entry(interpreter=str(real), approval="always")])

    def approver(request):
        with open(real, "ab") as stream:
            stream.write(b"\0")
        return True

    with pytest.raises(PolicyDenied, match="changed just before"):
        run_program(ctx(config, approver=approver), spec_of(config, work), {"word": "a"})


def test_program_replaced_by_a_link_after_trust(tmp_path):
    config, work, folder = trusted(tmp_path, [entry()])
    (tmp_path / "evil.sh").write_text(ECHO)
    os.chmod(tmp_path / "evil.sh", 0o755)
    os.unlink(folder / "echo.sh")
    os.symlink(tmp_path / "evil.sh", folder / "echo.sh")
    state = pm.program_states(config, work)[0]
    assert state.status == pm.INVALID and "symbolic link" in state.detail
    with pytest.raises(PolicyDenied):
        run_program(ctx(config), state.spec, {"word": "a"})


@pytest.mark.parametrize("program, expected", [("../evil.sh", "leaves the programs folder"),
                                               ("linkdir/echo.sh", "symbolic link")])
def test_program_paths_cannot_leave_or_use_links(tmp_path, program, expected):
    config, work, folder = setup(tmp_path, [entry(program=program)])
    (tmp_path / "evil.sh").write_text(ECHO)
    os.symlink(folder, folder / "linkdir")
    state = pm.program_states(config, work)[0]
    assert state.status == pm.INVALID and expected in state.detail


def test_duplicate_yaml_keys_refuse_the_manifest(tmp_path):
    config, work, folder = setup(tmp_path, [])
    (folder / "programs.yaml").write_text(
        "programs:\n  - name: echo_args\n    program: echo.sh\n    description: d\n    agent: linux_coder\n"
        "    class: read\n    class: delete\n    args: []\n")
    [state] = pm.program_states(config, work)
    assert state.name == pm.MANIFEST and state.status == pm.INVALID and "duplicate key 'class'" in state.detail


def test_duplicate_names_fail_instead_of_overriding(tmp_path):
    config, work, folder = setup(tmp_path, [entry()])
    register_program(entry(program=str(folder / "echo.sh"), description="track copy"), package="demo")
    try:
        [state] = pm.program_states(config, work)
        assert state.status == pm.INVALID and "both are refused" in state.detail
        with pytest.raises(pm.ProgramError, match="cannot be trusted"):
            pm.trust(config, "echo_args", work)
        specs, problems = attach_program_tools(config, [LINUX_CODER], work)
        assert "program_echo_args" not in specs[0].tool_names and problems
        with pytest.raises(ValueError, match="names must be unique"):
            register_program(entry(program=str(folder / "echo.sh"), description="another"), package="other")
    finally:
        unregister_program("echo_args")


@pytest.mark.real_program_defaults
def test_home_behind_a_link_uses_the_real_home(tmp_path, monkeypatch):
    """Linux check 25 Nov 2026: /home is a link on the user's machine; ~/.champion/programs was refused."""
    real_home = (tmp_path / "d" / "people" / "me").resolve()
    real_home.mkdir(parents=True)
    os.symlink(tmp_path / "d", tmp_path / "home")  # like /home -> /d
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "people" / "me"))
    work = (tmp_path / "work").resolve()
    work.mkdir()
    file = tmp_path / "config.yaml"  # no programs section: the real defaults ~/.champion/...
    file.write_text(yaml.safe_dump({"llm": {"model_id": "m"}, "execution": {"working_directory": str(work)}}))
    config = load_config(file, env={}, cwd=tmp_path)
    folder = real_home / ".champion" / "programs"
    assert config.get("programs.folder") == str(folder)
    assert config.get("programs.trust_file") == str(real_home / ".champion" / "programs.trust")
    folder.mkdir(parents=True, mode=0o700)
    (folder / "echo.sh").write_text(ECHO)
    os.chmod(folder / "echo.sh", 0o755)
    (folder / "programs.yaml").write_text(yaml.safe_dump({"programs": [entry()]}))
    assert [s.status for s in pm.program_states(config, work)] == [pm.UNTRUSTED]
    assert pm.trust(config, "echo_args", work).status == pm.TRUSTED
    assert "arg=a" in run_program(ctx(config), spec_of(config, work), {"word": "a"})
    linked = str(tmp_path / "home" / "people" / "me" / ".champion" / "programs")  # written by hand
    assert f"set programs.folder to the real path {folder}" in pm.folder_problem(linked, work)


def test_unsafe_trust_file_is_refused(tmp_path):
    config, work, _ = trusted(tmp_path, [entry()])
    os.chmod(tmp_path / "trust" / "programs.trust", 0o666)
    states = pm.program_states(config, work)
    assert states[0].name == "trust file" and states[0].status == pm.INVALID
    assert all(s.status != pm.TRUSTED for s in states)
    with pytest.raises(pm.ProgramError):
        pm.trust(config, "echo_args", work)
