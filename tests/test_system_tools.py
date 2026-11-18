"""Tests for process_list and disk_analysis (step 4a)."""

import os

import pytest

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError
from champion.tools.system import analyse_disk, list_processes, read_processes

PAGE = os.sysconf("SC_PAGE_SIZE")
TICKS = os.sysconf("SC_CLK_TCK")


@pytest.fixture
def config(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def ctx(config, level="1.1"):
    return ChampionContext.from_config(config, level=level, audit=AuditLog())


def fake_proc(root, processes):
    root.mkdir()
    (root / "uptime").write_text("1000.00 5.00\n")
    for pid, uid, comm, utime, start, rss_pages in processes:
        folder = root / str(pid)
        folder.mkdir()
        (folder / "stat").write_text(f"{pid} ({comm}) S 1 1 1 0 -1 0 0 0 0 0 {utime} 0 0 0 20 0 1 0 "
                                     f"{start} 1000 {rss_pages} 0 0 0\n")
        (folder / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        (folder / "cmdline").write_bytes(b"solver\x00--token=sk-secret-never-shown\x00")
    (root / "self").mkdir()
    return root


def test_process_list_parses_proc_and_hides_arguments(tmp_path, config):
    cfg, _ = config
    me, other = os.getuid(), os.getuid() + 1
    proc = fake_proc(tmp_path / "proc", [
        (101, me, "ls-dyna (mpp) x", 50 * TICKS, 500 * TICKS, 1000),
        (102, me, "python3", 400 * TICKS, 600 * TICKS, 10),
        (103, other, "big_other", 1 * TICKS, 100 * TICKS, 99999),
    ])
    rows = {p.pid: p for p in read_processes(proc)}
    assert rows[101].command == "ls-dyna (mpp) x" and rows[101].rss_bytes == 1000 * PAGE
    assert rows[102].cpu_percent == pytest.approx(100.0)  # 400 s CPU over 400 s alive
    by_memory = list_processes(ctx(cfg), proc=proc)
    lines = by_memory.splitlines()
    assert "2 of 2 shown" in lines[0] and "arguments and environment are not shown" in lines[0]
    assert "ls-dyna (mpp) x" in lines[2] and "python3" in lines[3]
    assert "big_other" not in by_memory and "sk-secret" not in by_memory and "--token" not in by_memory
    by_cpu = list_processes(ctx(cfg), sort="cpu", proc=proc).splitlines()
    assert "python3" in by_cpu[2]
    everyone = list_processes(ctx(cfg), all_users=True, limit=1, proc=proc)
    assert "all users" in everyone and "big_other" in everyone and "1 of 3 shown" in everyone
    with pytest.raises(ToolError, match="memory or cpu"):
        list_processes(ctx(cfg), sort="name", proc=proc)
    with pytest.raises(ToolError, match="/proc"):
        list_processes(ctx(cfg), proc=tmp_path / "no_proc")


def test_process_list_on_this_machine(config):
    cfg, _ = config
    out = list_processes(ctx(cfg), limit=5)
    assert out.splitlines()[1].split()[:3] == ["PID", "USER", "MEMORY"]
    assert str(os.getpid()) in list_processes(ctx(cfg), limit=200)  # this test run is one of them


def test_disk_analysis(config):
    cfg, work = config
    (work / "runs" / "a").mkdir(parents=True)
    (work / "runs" / "b").mkdir()
    (work / "runs" / "a" / "d3plot").write_bytes(b"x" * 300_000)
    (work / "runs" / "a" / "d3plot01").write_bytes(b"x" * 200_000)
    (work / "runs" / "b" / "main.k").write_bytes(b"x" * 1000)
    os.link(work / "runs" / "a" / "d3plot", work / "runs" / "b" / "same_d3plot")  # counted once
    (work / "secrets").mkdir()
    (work / "secrets" / "huge.bin").write_bytes(b"x" * 900_000)
    out = analyse_disk(ctx(cfg), ".", top=3)
    lines = out.splitlines()
    assert lines[0].startswith(f"{work}: 489.3 KiB in 3 file(s) and 3 folder(s)")
    assert "1 skipped (blocked" in lines[1] and "huge.bin" not in out
    folders = lines[lines.index("Largest folders (size including sub-folders):") + 1:lines.index("Largest files:")]
    assert [f.split()[-1] for f in folders] == ["runs/", "runs/a/", "runs/b/"]
    files = lines[lines.index("Largest files:") + 1:]
    assert files[0].endswith("runs/a/d3plot") and len(files) == 3
    with pytest.raises(PolicyDenied):
        analyse_disk(ctx(cfg), "/")
    with pytest.raises(ToolError, match="top"):
        analyse_disk(ctx(cfg), ".", top=0)
