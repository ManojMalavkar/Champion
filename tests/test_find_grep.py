"""Tests for the safe walker, filesystem_find and filesystem_grep (step 4a)."""

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
from champion.policy.scope import PolicyDenied
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError
from champion.tools.search import SEARCH_TOOLS, find_files, grep_files, parse_size
from champion.tools.walk import WalkStats, walk

PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"


@pytest.fixture
def tree(tmp_path):
    work = (tmp_path / "work").resolve()
    outside = (tmp_path / "outside").resolve()
    (work / "runs" / "a").mkdir(parents=True)
    (work / "runs" / "b" / "deep" / "x" / "y").mkdir(parents=True)
    outside.mkdir()
    (work / "main.k").write_text("*KEYWORD\n*INCLUDE\nruns/a/mat.k\n*END\n")
    (work / "runs" / "a" / "mat.k").write_text("*KEYWORD\n$ steel\n*MAT_024\n1 7.85e-9\n*mat_024\n*END\n")
    (work / "runs" / "a" / "run.dyn").write_text("*KEYWORD\n*CONTROL_TERMINATION\n")
    (work / "runs" / "b" / "deep" / "x" / "y" / "z.dyn").write_text("*KEYWORD\n")
    (work / "runs" / "big.k").write_text("*KEYWORD\n" + "$" * (2 * 1024 * 1024))
    old = work / "old.k"
    old.write_text("*KEYWORD\n*MAT_024\n")
    stamp = time.time() - 30 * 86400
    os.utime(old, (stamp, stamp))
    (work / ".ssh").mkdir()
    (work / ".ssh" / "id_rsa.k").write_text("*MAT_024\n")
    (work / "secrets").mkdir()
    (work / "secrets" / "hidden.k").write_text("*MAT_024\n")
    (work / ".env").write_text("MAT_024=sk-should-never-show\n")
    (work / ".champion").mkdir()
    (work / ".champion" / "backup.k").write_text("*MAT_024\n")
    (work / "data.bin").write_bytes(b"\x00\x01*MAT_024\x02")
    (work / "pasted.txt").write_text("*MAT_024 below\n" + PEM)
    (work / "license.key").write_text("ABCD-LICENSE *MAT_024 1234\n")
    (work / "link_out").symlink_to(outside)
    (outside / "far.k").write_text("*MAT_024\n")
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(folder / "config.yaml", env={}, cwd=tmp_path)
    return config, work, outside


def ctx(config, level="1.1"):
    return ChampionContext.from_config(config, level=level, audit=AuditLog())


def names(out: str) -> list[str]:
    return [line.split("  ")[-1] for line in out.splitlines()[2:]]


# ------------------------------------------------------------------ walker

def test_walk_skips_blocked_and_never_follows_links(tree):
    _, work, _ = tree
    stats = WalkStats()
    seen = {e.relative: e.kind for e in walk(work, max_depth=10, stats=stats)}
    assert seen["link_out"] == "link" and "link_out/far.k" not in seen
    assert not any(part in rel for rel in seen for part in (".ssh", "secrets", ".env", ".champion"))
    assert stats.blocked == 4 and stats.links == 1 and not stats.stopped
    assert "runs/b/deep/x/y/z.dyn" in seen


def test_walk_limits(tree):
    _, work, _ = tree
    stats = WalkStats()
    list(walk(work, max_depth=10, stats=stats, max_entries=3))
    assert stats.stopped == "3 entries scanned" and "STOPPED EARLY" in stats.summary()
    stats = WalkStats()
    list(walk(work, max_depth=10, stats=stats, deadline=time.monotonic() - 1))
    assert stats.stopped == "time limit reached"


# ------------------------------------------------------------------ find

def test_find_k_in_current_folder_only(tree):
    config, work, _ = tree
    out = find_files(ctx(config), ".", ["*.k"], recursive=False)
    assert out.splitlines()[0] == f"{work}: 2 matching files, all shown, sorted by name"
    assert names(out) == ["main.k", "old.k"]


def test_find_dyn_recursive_and_depth(tree):
    config, _, _ = tree
    assert names(find_files(ctx(config), ".", ["*.DYN"])) == ["runs/a/run.dyn", "runs/b/deep/x/y/z.dyn"]
    assert names(find_files(ctx(config), ".", ["*.dyn"], max_depth=3)) == ["runs/a/run.dyn"]
    assert names(find_files(ctx(config), ".", ["*.DYN"], case_sensitive=True)) == []


def test_find_size_date_sort_limit_and_type(tree):
    config, _, _ = tree
    assert names(find_files(ctx(config), ".", min_size="1M")) == ["runs/big.k"]
    assert "old.k" not in names(find_files(ctx(config), ".", ["*.k"], modified_within_days=7))
    by_size = names(find_files(ctx(config), ".", ["*.k"], sort="size"))
    assert by_size[0] == "runs/big.k"
    assert names(find_files(ctx(config), ".", ["*.k"], sort="modified"))[-1] == "old.k"
    limited = find_files(ctx(config), ".", ["*.k"], limit=1)
    assert ", 1 shown" in limited.splitlines()[0] and len(names(limited)) == 1
    folders = names(find_files(ctx(config), "runs", type="folder", max_depth=1))
    assert folders == ["a/", "b/"]
    anything = find_files(ctx(config), ".", type="any", max_depth=1)
    assert "link_out (symbolic link, not followed)" in anything
    assert "4 skipped (blocked" in anything and "1 symbolic link(s) not followed" in anything


def test_find_argument_checks(tree):
    config, _, _ = tree
    for bad in ({"patterns": ["runs/*.k"]}, {"patterns": ["../*.k"]}, {"limit": 0}, {"limit": 501},
                {"max_depth": 0}, {"type": "socket"}, {"sort": "owner"}, {"min_size": "lots"},
                {"min_size": "2M", "max_size": "1M"}, {"modified_within_days": 0}):
        with pytest.raises(ToolError):
            find_files(ctx(config), ".", **bad)
    assert parse_size("1.5 GiB", "x") == int(1.5 * 1024 ** 3) and parse_size("100k", "x") == 102400
    assert parse_size(2048, "x") == 2048 and parse_size(None, "x") is None


def test_find_follows_the_access_level(tree):
    config, _, outside = tree
    with pytest.raises(PolicyDenied, match="outside the working folder"):
        find_files(ctx(config, "1.1"), str(outside))
    assert names(find_files(ctx(config, "2.1"), str(outside))) == ["far.k"]
    for blocked in (".ssh", "secrets", ".champion"):
        with pytest.raises(PolicyDenied, match="always blocked|own folder"):
            find_files(ctx(config, "2.2"), blocked)
    with pytest.raises(PolicyDenied, match="symbolic links"):
        find_files(ctx(config, "2.2"), "link_out")
    with pytest.raises(PolicyDenied, match="system folder"):
        find_files(ctx(config, "2.2"), "/proc")


# ------------------------------------------------------------------ grep

def test_grep_mat_024_plain_text(tree):
    config, work, _ = tree
    out = grep_files(ctx(config), "*MAT_024")
    lines = out.splitlines()
    assert lines[0].startswith(f"{work}: 2 matching line(s) in 2 file(s)") and "plain text" in lines[0]
    assert "old.k:2: *MAT_024" in lines and "runs/a/mat.k:3: *MAT_024" in lines
    assert "1 binary file(s) skipped" in out and "2 file(s) skipped (private key or non-LS-DYNA .key)" in out
    for never in ("sk-should-never-show", "hidden.k", "id_rsa", "far.k", "backup.k", "LICENSE", "PRIVATE"):
        assert never not in out


def test_grep_options(tree):
    config, _, _ = tree
    assert "runs/a/mat.k:5: *mat_024" in grep_files(ctx(config), "*MAT_024", ignore_case=True)
    regex = grep_files(ctx(config), r"^\*MAT_0\d\d$", regex=True, patterns=["mat.k"])
    assert "1 matching line(s)" in regex and "runs/a/mat.k:3:" in regex
    one = grep_files(ctx(config), "*KEYWORD", max_matches=1)
    assert "1 matching line(s)" in one and "max_matches (1) reached" in one
    single = grep_files(ctx(config), "*INCLUDE", path="main.k")
    assert "main.k:2: *INCLUDE" in single and "1 file(s) searched" in single
    shallow = grep_files(ctx(config), "*KEYWORD", patterns=["*.dyn"], max_depth=3)
    assert "run.dyn" in shallow and "z.dyn" not in shallow
    with pytest.raises(ToolError, match="invalid regular expression"):
        grep_files(ctx(config), "(", regex=True)
    with pytest.raises(ToolError, match="must not be empty"):
        grep_files(ctx(config), "")


def test_grep_skips_large_files(tree):
    config, _, _ = tree
    config.data["pydyna"]["large_file_bytes"] = 1024 * 1024
    out = grep_files(ctx(config), "*KEYWORD", path="runs")
    assert "big.k" not in out and "1 file(s) of 1.0 MiB or more not searched" in out


def test_grep_follows_the_access_level(tree):
    config, _, outside = tree
    with pytest.raises(PolicyDenied, match="outside the working folder"):
        grep_files(ctx(config), "*MAT_024", path=str(outside))
    with pytest.raises(PolicyDenied, match="always blocked"):
        grep_files(ctx(config, "2.2"), "MAT", path=".env")
    assert "far.k:1: *MAT_024" in grep_files(ctx(config, "2.1"), "*MAT_024", path=str(outside))


# ------------------------------------------------------------------ as tools

def test_search_tools_run_through_the_guard(tree):
    config, _, outside = tree
    model = FakeModel([tool_call("filesystem_find", {"path": ".", "patterns": ["*.dyn"]}, call_id="c1"),
                       tool_call("filesystem_grep", {"text": "*MAT_024", "path": str(outside)}, call_id="c2"),
                       text("done")])
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(),
                              agent_factory=lambda m: Agent(name="Champion", tools=SEARCH_TOOLS, model=m))
    asyncio.run(runtime.ask("find"))
    outputs = [i["output"] for i in model.calls[2]["input"]
               if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert "runs/a/run.dyn" in outputs[0] and outputs[1].startswith("BLOCKED: ")
    rows = runtime.audit.recent()
    assert [(r["tool"], r["decision"]) for r in rows] == [("filesystem_find", "allow"), ("filesystem_grep", "block")]
    assert json.loads(rows[0]["arguments"])["patterns"] == ["*.dyn"]
