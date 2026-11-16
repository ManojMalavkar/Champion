"""Tests for the read-only file tools, the tool guard and their use by the agent (fake model)."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_model import FakeModel, text, tool_call

from champion import cli
from champion.chat import chat_loop
from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.core.runtime import ChampionRuntime
from champion.policy.scope import PolicyDenied
from champion.storage.audit import AuditLog
from champion.tools.filesystem import disk_usage, human_size, list_folder, read_text_file
from champion.tools.guard import ToolError, cap_output

PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    outside = (tmp_path / "outside").resolve()
    work.mkdir()
    outside.mkdir()
    (work / "model.k").write_text("*KEYWORD\n*NODE\n       1       0.0       0.0       0.0\n*END\n")
    (work / "main.key").write_text("$ comment\n*KEYWORD\n*INCLUDE\nmodel.k\n*END\n")
    (work / "ssh_copy.key").write_text(PEM)
    (work / "notes.txt").write_text("hello " * 50)
    (work / "pasted.txt").write_text("see below\n" + PEM)
    (work / "data.bin").write_bytes(b"\x00\x01\x02binary")
    (work / ".env").write_text("OPENAI_API_KEY=sk-should-never-show\n")
    (work / "runs").mkdir()
    (work / "link_out").symlink_to(outside)
    (outside / "far.txt").write_text("outside text\n")
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(folder / "config.yaml", env={}, cwd=tmp_path)
    return config, work, outside


def ctx(config, level="1.1"):
    return ChampionContext.from_config(config, level=level, audit=AuditLog())


# ------------------------------------------------------------- plain functions

def test_list_folder(setup):
    config, work, _ = setup
    out = list_folder(ctx(config), ".")
    assert out.splitlines()[0] == f"{work}: 8 entries, 1 hidden (secret-looking names)"
    assert "dir            -  runs/" in out
    assert "link_out (symbolic link, not followed)" in out
    assert "model.k" in out and ".env" not in out
    assert "first 2 shown" in list_folder(ctx(config), ".", limit=2)
    with pytest.raises(ToolError, match="between 1 and 500"):
        list_folder(ctx(config), ".", limit=0)
    with pytest.raises(ToolError, match="not a folder"):
        list_folder(ctx(config), "model.k")
    with pytest.raises(PolicyDenied, match="symbolic links"):
        list_folder(ctx(config, "2.1"), "link_out")


def test_read_text_file(setup):
    config, work, outside = setup
    out = read_text_file(ctx(config), "model.k")
    assert out.startswith(f"{work / 'model.k'} (") and "(whole file)" in out and "*NODE" in out
    assert "more not shown" in read_text_file(ctx(config), "notes.txt", max_chars=10)
    assert "*INCLUDE" in read_text_file(ctx(config), "main.key")  # LS-DYNA .key file: allowed
    with pytest.raises(PolicyDenied, match="private key"):
        read_text_file(ctx(config), "ssh_copy.key")
    with pytest.raises(PolicyDenied, match="private key"):
        read_text_file(ctx(config), "pasted.txt")
    with pytest.raises(ToolError, match="binary"):
        read_text_file(ctx(config), "data.bin")
    with pytest.raises(PolicyDenied, match="always blocked"):
        read_text_file(ctx(config, "2.2"), ".env")
    with pytest.raises(ToolError, match="does not exist"):
        read_text_file(ctx(config), "missing.k")
    with pytest.raises(ToolError, match="max_chars"):
        read_text_file(ctx(config), "model.k", max_chars=10**9)
    with pytest.raises(PolicyDenied, match="outside the working folder"):
        read_text_file(ctx(config, "1.2"), str(outside / "far.txt"))
    assert "outside text" in read_text_file(ctx(config, "2.1"), str(outside / "far.txt"))


def test_non_keyword_key_file_blocked(setup):
    config, work, _ = setup
    (work / "license.key").write_text("ABCD-EFGH-1234\n")
    with pytest.raises(PolicyDenied, match="not an LS-DYNA keyword file"):
        read_text_file(ctx(config), "license.key")


def test_large_files_refused(setup):
    config, work, _ = setup
    config.data["pydyna"]["large_file_bytes"] = 100
    (work / "big.k").write_text("*KEYWORD\n" + "$" * 200)
    with pytest.raises(ToolError, match="large-model tools"):
        read_text_file(ctx(config), "big.k")


def test_disk_usage_and_sizes(setup):
    config, work, _ = setup
    out = disk_usage(ctx(config), ".")
    assert out.startswith(f"{work}: total ") and "free" in out
    with pytest.raises(PolicyDenied):
        disk_usage(ctx(config), "/")
    assert human_size(512) == "512 B" and human_size(100 * 1024 * 1024) == "100.0 MiB"


def test_cap_output():
    assert cap_output("short", 100) == "short"
    capped = cap_output("x" * 500, 100)
    assert len(capped) <= 100 and capped.endswith("[output cut at 100 of 500 characters]")


# ------------------------------------------------------------- through the agent

def runtime_for(config, outputs, level=None):
    return ChampionRuntime(config, model=FakeModel(outputs), level=level, audit=AuditLog())


def tool_outputs(call):
    return [i["output"] for i in call["input"] if isinstance(i, dict) and i.get("type") == "function_call_output"]


def test_blocked_read_reaches_the_model_and_the_audit(setup):
    config, _, outside = setup
    far = str(outside / "far.txt")
    runtime = runtime_for(config, [tool_call("filesystem_read", {"path": far}),
                                   text("That file is outside your working folder.")])
    turn = asyncio.run(runtime.ask("read far.txt"))
    result = tool_outputs(runtime.model.calls[1])[0]
    assert result.startswith("BLOCKED: ") and "outside the working folder" in result
    assert "outside text" not in result
    assert turn.text == "That file is outside your working folder."
    row = runtime.audit.recent()[-1]
    assert (row["tool"], row["decision"], row["access_level"]) == ("filesystem_read", "block", "1.1")
    assert json.loads(row["arguments"])["path"] == far and row["agent"] == "Champion"


def test_level_change_in_chat_allows_the_read(setup):
    config, _, outside = setup
    far = str(outside / "far.txt")
    runtime = runtime_for(config, [tool_call("filesystem_read", {"path": far}, call_id="c1"), text("Blocked."),
                                   tool_call("filesystem_read", {"path": far}, call_id="c2"), text("It says outside text.")])
    lines, out = ["read far.txt", "/level", "/level 9.9", "/level 2.1", "read far.txt", "/audit"], []

    def read_line(prompt):
        if not lines:
            raise EOFError
        return lines.pop(0)

    asyncio.run(chat_loop(runtime, read_line, out.append))
    shown = "\n".join(out)
    assert "<- filesystem_read: BLOCKED: " in shown
    assert "Access level 1.1 (working folder, read only)" in shown
    assert "Unknown level 9.9" in shown
    assert "Access level is now 2.1" in shown
    assert "outside text" in tool_outputs(runtime.model.calls[3])[-1]
    assert [r["decision"] for r in runtime.audit.recent()] == ["block", "allow"]
    assert "block filesystem_read" in shown and "allow filesystem_read" in shown


def test_tools_hidden_when_level_gives_no_read(setup, tmp_path):
    config, _, _ = setup
    config.data["policy"]["custom"] = {"read": "none", "write": "none", "extra_dirs": []}
    runtime = runtime_for(config, [text("I cannot read files at this level.")], level="custom")
    asyncio.run(runtime.ask("list files"))
    assert runtime.model.calls[0]["tools"] == ["session_info"]  # is_enabled hid the file tools


def test_env_file_text_never_reaches_the_model(setup):
    config, _, _ = setup
    runtime = runtime_for(config, [tool_call("filesystem_read", {"path": ".env"}), text("Blocked.")], level="2.2")
    asyncio.run(runtime.ask("show .env"))
    sent = json.dumps(runtime.model.calls[1]["input"], default=str)
    assert "sk-should-never-show" not in sent and "always blocked" in sent


def test_cli_level_option():
    parser = cli.build_parser()
    assert parser.parse_args(["chat", "--level", "2.1"]).level == "2.1"
    with pytest.raises(SystemExit):
        parser.parse_args(["chat", "--level", "3.3"])
