"""Steps 4d.3-4d.7: file_create and file_write, hashes from reads and stale-edit
protection, multi-file change sets (check, stage, commit, rollback, one undo), the
Coder Agent with code_check / run_check / git_info, and hardening (links, traversal,
secrets, races, a large file, fault injection). Fake model, no network.
"""

import asyncio
import hashlib
import itertools
import json
import os
import subprocess
import sys
import tracemalloc

import pytest
import yaml
from agents import Agent
from fake_model import FakeModel, text, tool_call
from test_linux_specialist import tool_outputs

from champion.agents.orchestrator import orchestrator_instructions
from champion.chat import chat_loop
from champion.core.config import ConfigError, load_config
from champion.core.context import ChampionContext
from champion.core.runtime import ChampionRuntime
from champion.policy.scope import PolicyDenied
from champion.specialists.coder import CODER
from champion.specialists.registry import check_spec
from champion.storage.audit import AuditLog
from champion.tools import edit
from champion.tools.changes import changes_text, undo
from champion.tools.changeset import apply_change_set, change_set_apply
from champion.tools.checks import check_files, git_summary, run_named_check
from champion.tools.edit import create_file, edit_file_ops, file_create, file_edit, file_write, write_file
from champion.tools.filesystem import hash_file, read_text_file
from champion.tools.guard import ToolError

CODE = "import os\n\nTIMEOUT = 10\n\n\ndef run():\n    return os.getcwd()\n"
SETTINGS = "name: demo\ntimeout: 10\n"
TEST_APP = "from app import run\n\n\ndef test_run():\n    assert run()\n"
HAS_GIT = any(os.path.exists(f"{folder}/git") for folder in ("/usr/bin", "/bin"))
_ids = itertools.count(1)


def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def make(tmp_path, extra=None):
    work = (tmp_path / "work").resolve()
    work.mkdir(parents=True, exist_ok=True)
    data = {"llm": {"model_id": "m"}, "execution": {"working_directory": str(work)}, **(extra or {})}
    folder = tmp_path / "config"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text(yaml.safe_dump(data))
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


@pytest.fixture
def setup(tmp_path):
    config, work = make(tmp_path)
    (work / "app.py").write_text(CODE)
    (work / "settings.yaml").write_text(SETTINGS)
    return config, work


def ctx(config, level="1.2", approver=None):
    context = ChampionContext.from_config(config, level=level, audit=AuditLog())
    context.approver = approver
    return context


def yes(asked):
    return lambda request: asked.append(request) or True


def leftovers(folder):
    return [str(p) for p in folder.rglob("*") if ".champion-" in p.name]


def edits_app():
    return [{"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"}]


def change_set_files():
    return [{"path": "app.py", "action": "edit", "expected_sha256": sha(CODE), "edits": edits_app()},
            {"path": "tests/test_app.py", "action": "create", "content": TEST_APP, "create_parents": True},
            {"path": "settings.yaml", "action": "write", "expected_sha256": sha(SETTINGS),
             "content": "name: demo\ntimeout: 30\n"}]


# ------------------------------------------------------------------ 4d.3 file_create

def test_file_create_never_overwrites_and_undo_removes_it(setup):
    config, work = setup
    context = ctx(config)
    out = create_file(context, "helpers.py", "def helper():\n    return 1\n")
    assert out.startswith(f"Created {work / 'helpers.py'} (new file, 2 line(s)")
    assert f"File SHA-256 now {sha((work / 'helpers.py').read_bytes())}." in out and "+ def helper():" in out
    assert "file_create]" in changes_text(context) and " created " in changes_text(context)
    with pytest.raises(ToolError, match="already exists; file_create never overwrites"):
        create_file(context, "helpers.py", "other\n")
    assert "removed" in undo(context) and not (work / "helpers.py").exists()
    with pytest.raises(ToolError, match="set create_parents=true"):
        create_file(context, "pkg/sub/mod.py", "x = 1\n")
    dry = create_file(context, "pkg/sub/mod.py", "x = 1\n", create_parents=True, dry_run=True)
    assert dry.startswith("Dry run, nothing changed") and not (work / "pkg").exists()
    out = create_file(context, "pkg/sub/mod.py", "x = 1\n", create_parents=True)
    assert "Created folder(s):" in out and (work / "pkg" / "sub" / "mod.py").read_text() == "x = 1\n"
    undo(context)  # one group: the file and both folders
    assert not (work / "pkg").exists()
    create_file(context, "notes.md", "# Notes\n")
    (work / "notes.md").write_text("# Notes\nedited by the user\n")
    assert "NOT undone" in undo(context) and (work / "notes.md").exists()  # newer work is kept


def test_file_create_refusals(setup, tmp_path):
    config, work = setup
    context = ctx(config)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, work / "out")
    for path in (".env", ".champion/x.txt", "../outside/x.txt", str(outside / "x.txt"), "out/x.txt"):
        with pytest.raises((PolicyDenied, ToolError)):
            create_file(context, path, "x\n")
    with pytest.raises(PolicyDenied, match="private key"):
        create_file(context, "id.txt", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    with pytest.raises(ToolError, match="NUL"):
        create_file(context, "bin.txt", "a\x00b")
    with pytest.raises(PolicyDenied, match="does not allow editing"):
        create_file(ctx(config, "1.1"), "new.txt", "x\n")
    context.config.data["edit"]["max_write_bytes"] = 100
    with pytest.raises(ToolError, match="edit.max_write_bytes"):
        create_file(context, "big.txt", "x" * 101)
    assert list(outside.iterdir()) == [] and leftovers(work) == []
    assert sorted(p.name for p in work.iterdir()) == ["app.py", "out", "settings.yaml"]


def test_file_created_meanwhile_is_not_overwritten(setup):
    config, work = setup
    context = ctx(config)
    plan = edit.prepare_create(context, "race.txt", "from champion\n")
    try:
        (work / "race.txt").write_text("from the user\n")
        edit.stage_plan(context, plan, float("inf"))
        with pytest.raises(ToolError, match="created meanwhile"):
            edit.commit_plan(plan)
    finally:
        plan.close()
    assert (work / "race.txt").read_text() == "from the user\n" and leftovers(work) == []


# ------------------------------------------------------------------ 4d.3 file_write

def test_file_write_needs_the_hash_asks_and_undo_restores(setup):
    config, work = setup
    new = "name: demo\ntimeout: 30\nretries: 2\n"
    with pytest.raises(ToolError, match="needs expected_sha256"):
        write_file(ctx(config), "settings.yaml", new, expected_sha256=None)
    with pytest.raises(ToolError, match="changed since it was read"):
        write_file(ctx(config), "settings.yaml", new, expected_sha256="0" * 64)
    with pytest.raises(PolicyDenied, match="needs the user's approval"):
        write_file(ctx(config), "settings.yaml", new, expected_sha256=sha(SETTINGS))
    dry = write_file(ctx(config), "settings.yaml", new, expected_sha256=sha(SETTINGS), dry_run=True)
    assert dry.startswith("Dry run") and "+timeout: 30" in dry and (work / "settings.yaml").read_text() == SETTINGS
    asked = []
    context = ctx(config, approver=yes(asked))
    out = write_file(context, "settings.yaml", new, expected_sha256=sha(SETTINGS))
    assert out.startswith(f"Wrote {work / 'settings.yaml'}: whole file replaced")
    assert len(asked) == 1 and "replaces the whole file" in asked[0].note
    assert (work / "settings.yaml").read_text() == new and f"SHA-256 now {sha(new)}" in out
    assert "restored" in undo(context) and (work / "settings.yaml").read_text() == SETTINGS
    (work / "dos.txt").write_bytes(b"a\r\nb\r\n")
    write_file(context, "dos.txt", "a\nc\n", expected_sha256=sha(b"a\r\nb\r\n"))
    assert (work / "dos.txt").read_bytes() == b"a\r\nc\r\n"  # line endings kept
    with pytest.raises(ToolError, match="does not exist"):
        write_file(context, "missing.txt", "x", expected_sha256=sha("x"))
    context.config.data["edit"]["max_write_bytes"] = 10
    with pytest.raises(ToolError, match="edit.max_write_bytes"):
        write_file(context, "settings.yaml", "x\n", expected_sha256=sha(SETTINGS))


def test_tool_schemas():
    assert set(change_set_apply.params_json_schema["properties"]) == {"title", "files", "dry_run"}
    assert "FileChange" in change_set_apply.params_json_schema["$defs"]
    assert set(file_create.params_json_schema["properties"]) == {"path", "content", "create_parents", "dry_run"}
    assert "expected_sha256" in file_write.params_json_schema["required"]


# ------------------------------------------------------------------ 4d.4 hashes

def test_reads_return_the_sha256_and_start_line(setup):
    config, work = setup
    context = ctx(config, "1.1")
    out = read_text_file(context, "app.py")
    assert out.split("\n")[1] == f"SHA-256 {sha(CODE)}" and out.endswith(CODE)
    part = read_text_file(context, "app.py", start_line=3)
    assert part.split("\n")[0].endswith("lines 3-7 of 7")
    assert part.split("\n", 2)[2] == "TIMEOUT = 10\n\n\ndef run():\n    return os.getcwd()\n"
    assert "lines 3-3 of 7" in read_text_file(context, "app.py", max_chars=5, start_line=3).split("\n")[0]
    with pytest.raises(ToolError, match="has 7 line"):
        read_text_file(context, "app.py", start_line=8)
    assert hash_file(context, "app.py").endswith(f"SHA-256 {sha(CODE)}")


def test_agents_must_name_the_version_they_read(setup):
    config, work = setup
    change = {"path": "app.py", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"}
    model = FakeModel([tool_call("file_edit", change, call_id="c1"),
                       tool_call("file_edit", {**change, "dry_run": True}, call_id="c2"),
                       text("done")])
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(), level="1.2",
                              agent_factory=lambda m: Agent(name="Champion", tools=[file_edit], model=m))
    asyncio.run(runtime.ask("edit"))
    results = tool_outputs(runtime)
    assert any("expected_sha256 is required" in r for r in results)
    assert any(r.startswith("Dry run, nothing changed") for r in results)
    assert (work / "app.py").read_text() == CODE
    digest = sha(CODE)  # the agent read the file, then the user changed it
    (work / "app.py").write_text(CODE + "# typed by the user\n")
    with pytest.raises(ToolError, match="changed since it was read"):
        edit_file_ops(ctx(config), "app.py", edits_app(), expected_sha256=digest)
    assert (work / "app.py").read_text().endswith("# typed by the user\n")


# ------------------------------------------------------------------ 4d.5 change sets

def test_change_set_applies_all_files_and_one_undo_takes_them_back(setup):
    config, work = setup
    asked = []
    context = ctx(config, approver=yes(asked))
    dry = apply_change_set(context, "Raise timeout", change_set_files(), dry_run=True)
    assert dry.startswith("Dry run of change set 'Raise timeout', nothing changed: 3 file(s)") and not asked
    assert not (work / "tests").exists() and (work / "app.py").read_text() == CODE
    out = apply_change_set(context, "Raise timeout", change_set_files())
    assert out.startswith("Applied change set 'Raise timeout': 3 file(s), #1-#4 in /changes")
    assert len(asked) == 1 and "settings.yaml replaces the whole file" in asked[0].note
    assert "TIMEOUT = 30" in (work / "app.py").read_text()
    assert (work / "tests" / "test_app.py").read_text() == TEST_APP
    assert f"{work / 'app.py'}: SHA-256 now {sha((work / 'app.py').read_bytes())}" in out
    listing = changes_text(context)
    assert listing.count("change_set_apply]") == 4 and "Raise timeout: " in listing
    undo(context)
    assert (work / "app.py").read_text() == CODE and (work / "settings.yaml").read_text() == SETTINGS
    assert not (work / "tests").exists() and leftovers(work) == []


def test_change_set_checks_every_file_before_writing(setup):
    config, work = setup
    context = ctx(config)
    bad_anchor = change_set_files()[:2] + [
        {"path": "settings.yaml", "action": "edit", "expected_sha256": sha(SETTINGS),
         "edits": [{"operation": "insert_after", "anchor": "missing: key\n", "text": "x: 1\n"}]}]
    with pytest.raises(ToolError, match=r"file 3 \(settings.yaml\): .*anchor was not found.*nothing changed"):
        apply_change_set(context, "Bad", bad_anchor)
    cases = [
        ([change_set_files()[0], change_set_files()[0]], "listed twice"),
        ([{"path": "app.py", "action": "edit", "edits": edits_app()}], "expected_sha256 is required"),
        ([{"path": "app.py", "action": "create", "content": "x"}], "already exists"),
        ([{**change_set_files()[0], "content": "x"}], "does not take content"),
        ([{"path": "app.py", "action": "delete"}], "actions: edit, create, write"),
        ([], "non-empty list"),
    ]
    for files, message in cases:
        with pytest.raises(ToolError, match=message):
            apply_change_set(context, "Bad", files)
    with pytest.raises(PolicyDenied):
        apply_change_set(context, "Bad", [change_set_files()[0], {"path": ".env", "action": "create",
                                                                   "content": "A=1\n"}])
    assert (work / "app.py").read_text() == CODE and not (work / "tests").exists()
    assert "change_set_apply]" not in changes_text(context) and leftovers(work) == []


def test_commit_failure_rolls_back_the_files_already_written(setup, monkeypatch):
    config, work = setup
    context = ctx(config, approver=yes([]))
    real, calls = edit.commit_plan, []

    def flaky(plan):
        calls.append(plan.target.name)
        if len(calls) == 3:
            raise OSError(28, "No space left on device")
        real(plan)
    monkeypatch.setattr(edit, "commit_plan", flaky)
    with pytest.raises(ToolError, match=r"stopped at .*settings.yaml: .*No space left on device\. Rolled back 2 of 2"):
        apply_change_set(context, "Raise timeout", change_set_files())
    assert (work / "app.py").read_text() == CODE and (work / "settings.yaml").read_text() == SETTINGS
    assert not (work / "tests").exists() and leftovers(work) == []
    assert "change_set_apply]" not in changes_text(context)


def test_large_sets_ask_and_group_undo_checks_every_file_first(setup):
    config, work = setup
    for n in range(3):
        (work / f"m{n}.py").write_text(f"VALUE = {n}\n")
    files = [{"path": f"m{n}.py", "action": "edit", "expected_sha256": sha(f"VALUE = {n}\n"),
              "edits": [{"operation": "replace", "old_text": "VALUE", "new_text": "LIMIT"}]} for n in range(3)]
    config.data["edit"]["approval_max_files"] = 2
    with pytest.raises(PolicyDenied, match="needs the user's approval"):
        apply_change_set(ctx(config), "Rename VALUE", files)
    asked = []
    context = ctx(config, approver=yes(asked))
    apply_change_set(context, "Rename VALUE", files)
    assert "changes 3 files (more than edit.approval_max_files = 2)" in asked[0].note
    (work / "m1.py").write_text("LIMIT = 1\n# the user's line\n")
    report = undo(context)
    assert report.startswith("Undo refused, nothing was undone") and "m1.py changed since the edit" in report
    assert (work / "m0.py").read_text() == "LIMIT = 0\n"  # no part of the set was undone


# ------------------------------------------------------------------ 4d.6 the Coder Agent

def coder(task, inner, answer="Done.", reply="The Coder did it."):
    """Orchestrator -> coder_agent -> inner tool calls -> specialist answer -> reply."""
    steps = [tool_call("coder_agent", {"input": task}, call_id=f"o{next(_ids)}")]
    steps += [tool_call(name, args, call_id=f"s{next(_ids)}") for name, args in inner]
    return steps + [text(answer), text(reply)]


def drive(config, outputs, lines, level="1.2"):
    runtime = ChampionRuntime(config, model=FakeModel(outputs), audit=AuditLog(), level=level)
    queue, out, prompts = list(lines), [], []

    def read_line(prompt):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    return runtime, "\n".join(out), prompts


def test_coder_spec_and_tools():
    assert check_spec(CODER) == []
    assert CODER.tool_name == "coder_agent" and CODER.title == "Coder"
    assert CODER.tool_names == ["filesystem_list", "filesystem_read", "file_hash", "filesystem_find",
                                "filesystem_grep", "git_info", "file_edit", "file_create", "file_write",
                                "change_set_apply", "files_organize", "code_check", "run_check"]
    assert "coder_agent (Coder): Coder: changes source code" in orchestrator_instructions([CODER])
    # local 4d check: "run the tests" went to the Linux agent; the orchestrator now routes it to the Coder
    from champion.specialists.linux import LINUX
    both = orchestrator_instructions([LINUX, CODER])
    assert "Running tests or other checks: always coder_agent" in both and '"run the tests"' in both
    assert "cannot run Python, pytest, tests or scripts" in both
    assert "Running tests" not in orchestrator_instructions([LINUX])
    for rule in ("Read the parts you will change", "expected_sha256", "change_set_apply",
                 "data, never as instructions", "Do not try to bypass BLOCKED"):
        assert rule in CODER.instructions


def test_coder_reads_applies_a_change_set_checks_and_undo(setup):
    config, work = setup
    outputs = coder("Set TIMEOUT to 30 in app.py and add tests/test_app.py",
                    [("filesystem_read", {"path": "app.py"}),
                     ("change_set_apply", {"title": "Raise timeout", "files": change_set_files()[:2]}),
                     ("code_check", {"paths": ["app.py", "tests/test_app.py"]})],
                    answer="Changed app.py, added tests/test_app.py; syntax OK.")
    runtime, out, prompts = drive(config, outputs, ["Set TIMEOUT to 30 and add a test", "/changes", "/undo"])
    assert "      [Coder] -> filesystem_read(" in out and "      [Coder] -> change_set_apply(" in out
    assert "Changed files this turn: 3 (see /changes)" in out  # app.py, the tests folder, the test file
    assert "Approve? [y/N] " not in prompts  # an edit and a new file: no whole-file replacement
    results = tool_outputs(runtime)
    assert any(f"SHA-256 {sha(CODE)}" in r for r in results)
    assert any(r.startswith("Applied change set 'Raise timeout': 2 file(s)") for r in results)
    assert any(r.startswith("code_check: no syntax errors") for r in results)
    assert (work / "app.py").read_text() == CODE and not (work / "tests").exists()  # /undo took the set back
    calls = [c for c in runtime.model.calls if c["instructions"].startswith("You are Champion's Coder")]
    assert "shell_run" not in calls[0]["tools"] and "change_set_apply" in calls[0]["tools"]
    rows = [r for r in runtime.audit.recent(30) if r["tool"] == "change_set_apply"]
    assert rows and rows[0]["agent"] == "Coder" and "TIMEOUT = 30" not in json.dumps(rows)


def test_code_check_finds_syntax_errors_and_runs_nothing(setup, tmp_path):
    config, work = setup
    marker = tmp_path / "ran"
    (work / "good.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    (work / "bad.py").write_text("def f(:\n    pass\n")
    (work / "bad.json").write_text('{"a": 1,}')
    (work / "bad.yaml").write_text("a: [1, 2\n")
    (work / "bad.toml").write_text("a = \n")
    (work / "notes.txt").write_text("x")
    out = check_files(ctx(config, "1.1"), ["good.py", "bad.py", "bad.json", "bad.yaml", "bad.toml", "notes.txt",
                                           "settings.yaml"])
    assert out.startswith("code_check: 4 file(s) with errors (syntax only; nothing was run)")
    assert f"OK     {work / 'good.py'} (python)" in out and f"ERROR  {work / 'bad.py'} (python): line 1" in out
    assert f"SKIP   {work / 'notes.txt'}" in out and f"OK     {work / 'settings.yaml'} (yaml)" in out
    assert not marker.exists()  # compiled, never executed
    with pytest.raises(PolicyDenied):
        check_files(ctx(config, "1.1"), [str(tmp_path / "config" / "config.yaml")])


def test_a_declined_request_is_not_asked_again_in_the_same_turn(tmp_path):
    """Local 4d check: after the user answered n, the orchestrator called the Coder again and the
    same run_check prompt appeared a second time."""
    config, work = make(tmp_path, {"coder": {"checks": {"tests": ["/usr/bin/true"]}}})
    asked = []
    context = ctx(config, approver=lambda request: asked.append(request) and False)
    with pytest.raises(PolicyDenied, match="answered no at the approval prompt"):
        run_named_check(context, "tests")
    with pytest.raises(PolicyDenied, match="already declined this exact request in this turn"):
        run_named_check(context, "tests")  # the retry is refused without a prompt
    assert len(asked) == 1
    context.declined = set()  # the next turn (ChampionRuntime.ask) asks again
    with pytest.raises(PolicyDenied, match="answered no"):
        run_named_check(context, "tests")
    assert len(asked) == 2


def test_run_check_runs_only_configured_checks_with_approval(tmp_path):
    checks = {"ok": [sys.executable, "-c", "print('all fine')"],
              "fails": [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"]}
    config, work = make(tmp_path, {"coder": {"checks": checks}})
    with pytest.raises(ToolError, match=r"no check named 'tests' \(configured: fails, ok\)"):
        run_named_check(ctx(config), "tests")
    with pytest.raises(PolicyDenied, match="does not allow running checks"):
        run_named_check(ctx(config, "1.1"), "ok")
    with pytest.raises(PolicyDenied, match="needs the user's approval"):
        run_named_check(ctx(config), "ok")
    asked = []
    out = run_named_check(ctx(config, approver=yes(asked)), "ok")
    assert "all fine" in out and out.endswith("Check 'ok': PASSED")
    assert asked[0].command_class == "check" and asked[0].argv == tuple(checks["ok"])
    assert "runs project code" in asked[0].describe()
    out = run_named_check(ctx(config, approver=yes(asked)), "fails")
    assert "exit code 3" in out and "boom" in out and out.endswith("Check 'fails': FAILED")
    empty, _ = make(tmp_path / "other")
    with pytest.raises(ToolError, match="configured: none"):
        run_named_check(ctx(empty), "tests")
    for bad in ({"x y": ["ls"]}, {"t": []}, {"t": ["bin/python"]}, {"t": "pytest"}):
        with pytest.raises(ConfigError, match="coder.checks"):
            make(tmp_path / "bad", {"coder": {"checks": bad}})


def test_run_check_passes_only_the_listed_environment(tmp_path, monkeypatch):
    """Local 4d check: a Python built with shared libraries needs LD_LIBRARY_PATH (exit code 127)."""
    show = ["/usr/bin/env"]  # a system program: it starts without LD_LIBRARY_PATH on every machine
    monkeypatch.setenv("LD_LIBRARY_PATH", os.environ.get("LD_LIBRARY_PATH", "/opt/champion-test/lib"))
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret")
    monkeypatch.setenv("CHAMPION_EXTRA", "x")
    config, _ = make(tmp_path, {"coder": {"checks": {"env": show}}})
    out = run_named_check(ctx(config, approver=yes([])), "env")
    assert out.endswith("Check 'env': PASSED")
    assert f"LD_LIBRARY_PATH={os.environ['LD_LIBRARY_PATH']}" in out
    assert "DATABRICKS_TOKEN" not in out and "dapi-secret" not in out and "CHAMPION_EXTRA" not in out
    config, _ = make(tmp_path / "none", {"coder": {"checks": {"env": show}, "check_env": []}})
    out = run_named_check(ctx(config, approver=yes([])), "env")
    assert out.endswith("Check 'env': PASSED") and "PATH=/usr/bin:/bin" in out and "HOME=" in out
    assert "LD_LIBRARY_PATH=" not in out and "CHAMPION_EXTRA" not in out  # only the fixed clean environment
    for bad in (["DATABRICKS_TOKEN"], ["PATH"], ["LD_PRELOAD"], ["1X"], "LD_LIBRARY_PATH"):
        with pytest.raises(ConfigError, match="coder.check_env"):
            make(tmp_path / "bad", {"coder": {"check_env": bad}})


def test_champion_config_is_readable_but_never_edited(tmp_path):
    """Local 4d check 9: the Coder read config/config.yaml inside the working folder; it must
    never edit it (approval limits, check commands). It is told which checks exist."""
    from agents import RunContextWrapper

    from champion.agents.orchestrator import build_specialist_agent

    work = (tmp_path / "work").resolve()
    (work / "config").mkdir(parents=True)
    (work / "config" / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"model_id": "m"}, "execution": {"working_directory": str(work)},
        "coder": {"checks": {"tests": ["/usr/bin/true"]}}}))
    config = load_config(work / "config" / "config.yaml", env={}, cwd=tmp_path)
    context = ctx(config, approver=yes([]))
    digest = read_text_file(context, "config/config.yaml").split("\n")[1].split()[1]  # reading is fine
    attempts = [
        lambda: edit_file_ops(context, "config/config.yaml", [{"operation": "replace", "old_text": "true",
                                                                "new_text": "false"}], expected_sha256=digest),
        lambda: write_file(context, "config/config.yaml", "x: 1\n", expected_sha256=digest),
        lambda: apply_change_set(context, "t", [{"path": "./config/../config/config.yaml", "action": "write",
                                                 "expected_sha256": digest, "content": "x: 1\n"}]),
    ]
    for attempt in attempts:
        with pytest.raises(PolicyDenied, match="Champion's own config file"):
            attempt()
    assert "/usr/bin/true" in (work / "config" / "config.yaml").read_text()
    agent = build_specialist_agent(CODER, FakeModel([]))
    assert agent.instructions(RunContextWrapper(context), agent).endswith("Configured checks for run_check: tests.")
    empty = ctx(make(tmp_path / "empty")[0])
    assert "Configured checks for run_check: none." in agent.instructions(RunContextWrapper(empty), agent)


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_git_info_is_read_only(setup):
    config, work = setup
    with pytest.raises(ToolError, match="not inside a Git repository"):
        git_summary(ctx(config, "1.1"))
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    out = git_summary(ctx(config, "1.1"))
    assert out.startswith(f"Repository {work}, branch ") and "?? app.py" in out and "never commits" in out


# ------------------------------------------------------------------ 4d.7 hardening

def test_file_changed_between_check_and_commit_is_refused(setup):
    config, work = setup
    context = ctx(config)
    plan = edit.prepare_edit(context, "app.py", edits_app(), sha(CODE))
    try:
        with open(work / "app.py", "a") as stream:
            stream.write("# typed by the user\n")
        edit.stage_plan(context, plan, float("inf"))
        with pytest.raises(ToolError, match="changed while it was being edited"):
            edit.commit_plan(plan)
    finally:
        plan.close()
    assert (work / "app.py").read_text() == CODE + "# typed by the user\n" and leftovers(work) == []


def test_change_set_with_a_large_file_streams(setup):
    config, work = setup
    block = b"1 0.0 0.0 0.0\n" * 1000
    with open(work / "big.k", "wb") as stream:
        stream.write(b"*KEYWORD\n*NODE\n")
        for _ in range(1500):  # about 20 MiB
            stream.write(block)
        stream.write(b"*END\n")
    digest = hash_file(ctx(config, "1.1"), "big.k").rsplit(" ", 1)[1]
    files = [{"path": "big.k", "action": "edit", "expected_sha256": digest,
              "edits": [{"operation": "insert_after", "anchor": "*KEYWORD\n", "text": "$ checked by Champion\n"},
                        {"operation": "replace", "old_text": "*END\n", "new_text": "*END\n$ end\n"}]},
             change_set_files()[0]]
    tracemalloc.start()
    try:
        out = apply_change_set(ctx(config), "Mark the deck", files)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert out.startswith("Applied change set 'Mark the deck': 2 file(s)")
    assert peak < 12 * 1024 * 1024, peak
    with open(work / "big.k", "rb") as stream:
        assert stream.read(31) == b"*KEYWORD\n$ checked by Champion\n"


def test_staging_failures_change_nothing(setup, monkeypatch):
    config, work = setup
    context = ctx(config, approver=yes([]))
    real, calls = os.fsync, []

    def failing(fd):
        calls.append(fd)
        if len(calls) == 2:  # the second staged file
            raise OSError(5, "Input/output error")
        real(fd)
    monkeypatch.setattr(os, "fsync", failing)
    with pytest.raises(OSError, match="Input/output error"):
        apply_change_set(context, "Raise timeout", change_set_files())
    monkeypatch.setattr(os, "fsync", real)
    assert (work / "app.py").read_text() == CODE and not (work / "tests").exists() and leftovers(work) == []

    class Full:
        f_bavail, f_frsize = 1, 1
    monkeypatch.setattr(os, "fstatvfs", lambda fd: Full())
    with pytest.raises(ToolError, match="not enough free disk space"):
        apply_change_set(context, "Raise timeout", change_set_files())
    assert (work / "app.py").read_text() == CODE and not (work / "tests").exists() and leftovers(work) == []
    assert "change_set_apply]" not in changes_text(context)
