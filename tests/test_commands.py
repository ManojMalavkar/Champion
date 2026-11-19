"""Tests for the executor (commands.py), approval and the shell tools (step 4a)."""

import asyncio
import json
import os
import time

import pytest
from agents import Agent
from fake_model import FakeModel, text, tool_call

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.core.runtime import ChampionRuntime
from champion.execution.commands import Limits, resolve, run_steps
from champion.policy.scope import PolicyDenied
from champion.storage.audit import AuditLog
from champion.tools.changes import changes_text, undo
from champion.tools.guard import ToolError
from champion.tools.shell import SHELL_TOOLS, run_pipeline, run_shell

PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----\n"
LIMITS = Limits(timeout_seconds=20, cpu_seconds=30, memory_bytes=2 * 1024 ** 3, file_bytes=1024 ** 3,
                output_chars=12000)


@pytest.fixture
def env(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    (work / "a.k").write_text("*KEYWORD\n*MAT_024\n*END\n")
    (work / "b.k").write_text("*KEYWORD\n*END\n")
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(folder / "config.yaml", env={}, cwd=tmp_path)
    return config, work


def ctx(config, level="1.1", approver=None, audit=None):
    context = ChampionContext.from_config(config, level=level, audit=audit or AuditLog())
    context.approver = approver
    return context


# ------------------------------------------------------------------ executor

def test_no_shell_metacharacters_are_plain_text(env):
    _, work = env
    for argument in ("a.k; touch pwned", "$(touch pwned)", "`touch pwned`", "a.k && touch pwned", "a.k > pwned"):
        result = run_steps([["ls", argument]], str(work), LIMITS)
        assert result.exit_code != 0 and "No such file" in result.stderr
    assert not (work / "pwned").exists()
    assert resolve("ls").startswith(("/usr/bin/", "/bin/"))
    with pytest.raises(ToolError, match="not installed"):
        resolve("no-such-program-champion")


def test_child_environment_is_clean(env, monkeypatch):
    _, work = env
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi" + "0" * 32)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-never-in-child")
    monkeypatch.setenv("POSIXLY_CORRECT", "1")
    result = run_steps([["env"]], str(work), LIMITS)
    names = sorted(line.split("=", 1)[0] for line in result.stdout.splitlines())
    assert names == ["HOME", "LANG", "LC_ALL", "PATH", "TZ"]
    assert "PATH=/usr/bin:/bin" in result.stdout and "champion-home-" in result.stdout


def test_timeout_kills_the_process_group(env):
    _, work = env
    limits = Limits(1, 30, 2 * 1024 ** 3, 1024 ** 3, 12000)
    started = time.monotonic()
    result = run_steps([["sh", "-c", "sleep 30 & echo $!; wait"]], str(work), limits)
    assert result.timed_out and time.monotonic() - started < 6
    child = int(result.stdout.split()[0])
    time.sleep(0.2)
    try:
        with open(f"/proc/{child}/stat") as stream:
            state = stream.read().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        state = "gone"
    assert state in ("gone", "Z"), f"child sleep still running ({state})"


def test_output_is_capped_and_secrets_hidden(env):
    _, work = env
    result = run_steps([["seq", "1", "2000000"]], str(work), LIMITS)
    assert result.truncated and len(result.stdout) <= 12000 and result.output_bytes > 10_000_000
    (work / "pasted.txt").write_text("before\n" + PEM + "after dapi" + "a" * 32 + "\n")
    shown = run_steps([["head", "-n", "20", "pasted.txt"]], str(work), LIMITS)
    assert shown.redacted and "MIIEabc" not in shown.stdout and "[private key hidden by Champion]" in shown.stdout
    assert "dapi" + "a" * 32 not in shown.stdout and "before" in shown.stdout


def test_file_size_limit_applies(env):
    _, work = env
    (work / "big.bin").write_bytes(b"x" * 50_000)
    limits = Limits(20, 30, 2 * 1024 ** 3, 10_000, 12000)
    result = run_steps([["cp", "big.bin", "copy.bin"]], str(work), limits)
    assert result.exit_code != 0 and (work / "copy.bin").stat().st_size <= 10_000


def test_pipeline_runs_without_a_shell(env):
    _, work = env
    result = run_steps([["seq", "1", "100"], ["sort", "-n", "-r"], ["head", "-n", "3"]], str(work), LIMITS)
    assert result.stdout.split() == ["100", "99", "98"] and result.exit_codes[1:] == [0, 0]


# ------------------------------------------------------------------ shell tools

def test_read_command_at_read_only(env):
    config, _ = env
    out = run_shell(ctx(config), ["wc", "-l", "*.k"])
    assert out.splitlines()[0] == "$ wc -l a.k b.k" and "exit code 0" in out and "5 total" in out
    found = run_shell(ctx(config), ["find", ".", "-name", "*.k", "-type", "f"])
    assert "./a.k" in found and "./b.k" in found
    grep = run_shell(ctx(config), ["grep", "-n", "*MAT_024", "a.k"])
    assert "2:*MAT_024" in grep
    with pytest.raises(PolicyDenied, match="does not allow edit commands"):
        run_shell(ctx(config), ["mkdir", "results"])
    with pytest.raises(PolicyDenied):
        run_shell(ctx(config), ["ls"], cwd="..")


def test_write_at_write_level_runs_without_prompt_and_is_listed(env):
    config, work = env
    asked = []
    context = ctx(config, "1.2", approver=lambda request: asked.append(request) or False)
    out = run_shell(context, ["mkdir", "-p", "results/run1"])
    assert "exit code 0" in out and "#1 in /changes" in out and (work / "results" / "run1").is_dir()
    run_shell(context, ["cp", "a.k", "b.k", "results"])
    assert (work / "results" / "a.k").exists() and asked == []
    listing = changes_text(context)
    assert "created" in listing and "copied" in listing and "[no undo]" in listing
    with pytest.raises(ToolError, match="cannot be undone"):
        undo(context)
    failed = run_shell(context, ["cp", "missing.k", "results"])
    assert "exit code 1" in failed and "in /changes" not in failed  # nothing changed, nothing listed


def test_delete_asks_every_time_and_denial_runs_nothing(env):
    config, work = env
    (work / "old.k").write_text("x")
    with pytest.raises(PolicyDenied, match="non-interactive"):
        run_shell(ctx(config, "1.2"), ["rm", "old.k"])
    requests = []
    with pytest.raises(PolicyDenied, match="did not approve"):
        run_shell(ctx(config, "1.2", approver=lambda r: requests.append(r) or False), ["rm", "old.k"])
    assert (work / "old.k").exists()
    text_shown = requests[0].describe()
    assert "rm old.k" in text_shown and str(work / "old.k") in text_shown and "cannot be restored" in text_shown
    context = ctx(config, "1.2", approver=lambda r: requests.append(r) or True)
    out = run_shell(context, ["rm", "old.k"])
    assert "exit code 0" in out and not (work / "old.k").exists() and "deleted" in changes_text(context)
    assert len(requests) == 2 and requests[1].fingerprint == requests[0].fingerprint
    config.data["execution"]["require_approval"] = False
    (work / "old.k").write_text("x")
    with pytest.raises(PolicyDenied, match="refused"):
        run_shell(ctx(config, "1.2", approver=lambda r: True), ["rm", "old.k"])
    assert (work / "old.k").exists()


def test_command_that_changes_after_approval_does_not_run(env):
    config, work = env
    (work / "x1.tmp").write_text("x")

    def sneaky(request):
        (work / "x2.tmp").write_text("appeared while the user was reading")
        return True
    with pytest.raises(PolicyDenied, match="changed between the approval and the start"):
        run_shell(ctx(config, "1.2", approver=sneaky), ["rm", "*.tmp"])
    assert (work / "x1.tmp").exists() and (work / "x2.tmp").exists()


def test_pipeline_tool_is_read_only(env):
    config, _ = env
    out = run_pipeline(ctx(config), [{"argv": ["grep", "-c", "*", "a.k", "b.k"]}, {"argv": ["sort", "-r"]}])
    assert out.splitlines()[0] == "$ grep -c '*' a.k b.k | sort -r" and "a.k:3" in out
    with pytest.raises(PolicyDenied, match="pipelines are read-only"):
        run_pipeline(ctx(config, "1.2"), [{"argv": ["ls"]}, {"argv": ["mkdir", "x"]}])
    with pytest.raises(ToolError, match="2 to 5 steps"):
        run_pipeline(ctx(config), [{"argv": ["ls"]}])


def test_shell_tools_through_the_guard_and_audit(env):
    config, work = env
    model = FakeModel([tool_call("shell_run", {"argv": ["wc", "-l", "*.k"]}, call_id="c1"),
                       tool_call("shell_run", {"argv": ["bash", "-c", "cat ~/.ssh/id_rsa"]}, call_id="c2"),
                       tool_call("shell_run", {"argv": ["rm", "a.k"]}, call_id="c3"),
                       tool_call("shell_pipeline", {"steps": [{"argv": ["ls"]}, {"argv": ["wc", "-l"]}]},
                                 call_id="c4"),
                       text("done")])
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(), level="1.2",
                              agent_factory=lambda m: Agent(name="Champion", tools=SHELL_TOOLS, model=m))
    asyncio.run(runtime.ask("go"))
    outputs = [i["output"] for i in model.calls[-1]["input"]
               if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert "5 total" in outputs[0]
    assert outputs[1].startswith("BLOCKED: 'bash' is never run by Champion")
    assert outputs[2].startswith("BLOCKED: this delete command needs the user's approval")
    assert "exit code 0" in outputs[3]
    assert (work / "a.k").exists()
    rows = runtime.audit.recent()
    assert [(r["tool"], r["decision"]) for r in rows] == [("shell_run", "allow"), ("shell_run", "block"),
                                                          ("shell_run", "block"), ("shell_pipeline", "allow")]
    first = json.loads(rows[0]["arguments"])
    assert first["argv"] == ["wc", "-l", "a.k", "b.k"] and first["class"] == "read" and first["exit_code"] == 0
    assert str(work / "a.k") in first["paths"]
    assert json.loads(rows[2]["arguments"])["class"] == "delete"
    assert all("KEYWORD" not in r["arguments"] for r in rows)  # no command output in the audit


def test_shell_tools_offered_at_read_only(env):
    config, _ = env
    model = FakeModel([text("ok")])
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(), level="1.1",
                              agent_factory=lambda m: Agent(name="Champion", tools=SHELL_TOOLS, model=m))
    asyncio.run(runtime.ask("hi"))
    assert model.calls[0]["tools"] == ["shell_run", "shell_pipeline"]
