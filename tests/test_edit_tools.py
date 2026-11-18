"""Tests for file_edit (streaming patch), files_organize, the change list and /undo (step 4a)."""

import asyncio
import os
import tracemalloc

import pytest
from agents import Agent
from fake_model import FakeModel, text, tool_call

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.core.runtime import ChampionRuntime
from champion.policy.scope import PolicyDenied
from champion.storage.audit import AuditLog
from champion.tools import text_patch
from champion.tools.changes import changes_text, turn_summary, undo
from champion.tools.edit import EDIT_TOOLS, edit_file, organize_files
from champion.tools.guard import ToolError

MODEL = "*KEYWORD\n*PART\n$ pid secid mid\n1 1 1\n*MAT_ELASTIC\n1 7.85e-9 210000.0 0.3\n*END\n"


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    outside = (tmp_path / "outside").resolve()
    work.mkdir()
    outside.mkdir()
    (work / "main.k").write_text(MODEL)
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(folder / "config.yaml", env={}, cwd=tmp_path)
    return config, work, outside


def ctx(config, level="1.2", audit=None):
    return ChampionContext.from_config(config, level=level, audit=audit or AuditLog())


def leftovers(folder):
    return [p.name for p in folder.iterdir() if ".champion-" in p.name]


# ------------------------------------------------------------------ file_edit

def test_edit_replaces_once_shows_diff_and_undo_restores(setup):
    config, work, _ = setup
    context = ctx(config)
    out = edit_file(context, "main.k", "1 7.85e-9 210000.0 0.3\n", "1 7.85e-9 200000.0 0.3\n")
    assert out.startswith(f"Edited {work / 'main.k'} at line 6: 1 line(s) replaced by 1 line(s)")
    assert "Change #1 in /changes; /undo restores it." in out
    assert "- 1 7.85e-9 210000.0 0.3" in out and "+ 1 7.85e-9 200000.0 0.3" in out and "  *MAT_ELASTIC" in out
    assert (work / "main.k").read_text() == MODEL.replace("210000.0", "200000.0")
    listing = changes_text(context)
    assert "#1" in listing and "modified" in listing and "line 6: 1 line(s) replaced by 1" in listing
    assert turn_summary(context, 0) == "  Changed files this turn: 1 (see /changes)"
    backups = list((work / ".champion" / "backups" / context.audit.session_id).iterdir())
    assert len(backups) == 1 and oct(backups[0].stat().st_mode)[-3:] == "600"
    report = undo(context)
    assert "#1:" in report and "restored" in report
    assert (work / "main.k").read_text() == MODEL
    assert "[undone by #2]" in changes_text(context)
    with pytest.raises(ToolError, match="nothing left to undo"):
        undo(context)
    assert leftovers(work) == []


def test_undo_refuses_when_file_changed_since(setup):
    config, work, _ = setup
    context = ctx(config)
    edit_file(context, "main.k", "*PART\n", "*PART\n$ edited\n")
    with open(work / "main.k", "a") as stream:
        stream.write("$ user typed this\n")
    report = undo(context, 1)
    assert "NOT undone" in report and "changed since the edit" in report
    assert (work / "main.k").read_text().endswith("$ user typed this\n")


def test_edit_needs_exactly_one_match(setup):
    config, work, _ = setup
    with pytest.raises(ToolError, match="not found"):
        edit_file(ctx(config), "main.k", "*MAT_024\n", "x")
    with pytest.raises(ToolError, match=r"matches 3 times .* \(first at lines 4, 4, 6\)"):
        edit_file(ctx(config), "main.k", "1 ", "2 ")
    with pytest.raises(ToolError, match="same"):
        edit_file(ctx(config), "main.k", "*END", "*END")
    with pytest.raises(ToolError, match="must not be empty"):
        edit_file(ctx(config), "main.k", "", "x")
    assert (work / "main.k").read_text() == MODEL


def test_match_across_chunk_border_and_counts(setup, monkeypatch):
    config, work, _ = setup
    monkeypatch.setattr(text_patch, "CHUNK", 7)
    data = "0123456789" * 5 + "\n*MAT_024\n" + "z" * 30 + "\n"
    (work / "m.k").write_text(data)
    fd = os.open(work / "m.k", os.O_RDONLY)
    try:
        found = text_patch.scan(fd, b"*MAT_024", deadline=float("inf"))
        overlapping = text_patch.scan(fd, b"zz", deadline=float("inf"))
    finally:
        os.close(fd)
    assert (found.count, found.offsets, found.lines) == (1, [51], [2])
    assert overlapping.count == 29
    edit_file(ctx(config), "m.k", "*MAT_024", "*MAT_PIECEWISE_LINEAR_PLASTICITY")
    assert (work / "m.k").read_text() == data.replace("*MAT_024", "*MAT_PIECEWISE_LINEAR_PLASTICITY")


def test_edit_keeps_crlf_and_latin1(setup):
    config, work, _ = setup
    (work / "dos.k").write_bytes(b"*KEYWORD\r\n*PART\r\n1\r\n*END\r\n")
    edit_file(ctx(config), "dos.k", "*PART\n1\n", "*PART\n2\n$ new\n")
    assert (work / "dos.k").read_bytes() == b"*KEYWORD\r\n*PART\r\n2\r\n$ new\r\n*END\r\n"
    (work / "latin.k").write_bytes("*KEYWORD\n$ M\u00fcller\n*END\n".encode("latin-1"))
    edit_file(ctx(config), "latin.k", "$ M\u00fcller", "$ Gr\u00f6\u00dfe")
    assert (work / "latin.k").read_bytes() == "*KEYWORD\n$ Gr\u00f6\u00dfe\n*END\n".encode("latin-1")
    with pytest.raises(ToolError, match="encoding"):
        edit_file(ctx(config), "latin.k", "*END", "*END \u2713")


def test_edit_refusals_leave_the_file_unchanged(setup, monkeypatch):
    config, work, outside = setup
    (work / "data.bin").write_bytes(b"\x00\x01abc")
    with pytest.raises(ToolError, match="binary"):
        edit_file(ctx(config), "data.bin", "abc", "x")
    with pytest.raises(PolicyDenied, match="does not allow writing"):
        edit_file(ctx(config, "1.1"), "main.k", "*END", "*END\n")
    (outside / "o.k").write_text("*END\n")
    with pytest.raises(PolicyDenied, match="outside the working folder"):
        edit_file(ctx(config, "1.2"), str(outside / "o.k"), "*END", "x")
    (work / "link.k").symlink_to(work / "main.k")
    with pytest.raises(PolicyDenied, match="symbolic link"):
        edit_file(ctx(config), "link.k", "*END", "x")
    with pytest.raises(PolicyDenied, match="own folder"):
        edit_file(ctx(config), ".champion/backups/x.json", "a", "b")
    config.data["pydyna"]["max_model_bytes"] = 10
    with pytest.raises(ToolError, match="safety cap"):
        edit_file(ctx(config), "main.k", "*END", "x")
    config.data["pydyna"]["max_model_bytes"] = 8 * 1024 ** 3

    class Tiny:
        f_bavail, f_frsize = 1, 1
    monkeypatch.setattr(os, "fstatvfs", lambda fd: Tiny())
    with pytest.raises(ToolError, match="not enough free disk space"):
        edit_file(ctx(config), "main.k", "*END", "x")
    monkeypatch.undo()
    config.data["pydyna"]["model_timeout_seconds"] = 0
    with pytest.raises(ToolError, match="time limit"):
        edit_file(ctx(config), "main.k", "*END", "x")
    assert (work / "main.k").read_text() == MODEL and leftovers(work) == []


def test_edit_keeps_permissions_and_is_refused_at_2_1(setup):
    config, work, _ = setup
    os.chmod(work / "main.k", 0o640)
    edit_file(ctx(config), "main.k", "*END", "*END\n$ kept")
    assert oct((work / "main.k").stat().st_mode)[-3:] == "640"
    with pytest.raises(PolicyDenied, match="does not allow writing"):
        edit_file(ctx(config, "2.1"), "main.k", "$ kept", "$ no")


def test_failure_during_write_removes_the_temporary_file(setup, monkeypatch):
    config, work, _ = setup

    def broken(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(OSError):
        edit_file(ctx(config), "main.k", "*END", "*END\n$ more")
    assert (work / "main.k").read_text() == MODEL and leftovers(work) == []


def test_edit_memory_stays_bounded_on_a_large_file(setup):
    config, work, _ = setup
    big = work / "big.k"
    line = b"       1       1       1       1       1       1       1       1\n"
    with open(big, "wb") as stream:
        stream.write(b"*KEYWORD\n*NODE\n")
        for _ in range(40):
            stream.write(line * 16384)  # 40 x 1 MiB
        stream.write(b"*MAT_ELASTIC\n1 7.85e-9 210000.0 0.3\n*END\n")
    size = big.stat().st_size
    tracemalloc.start()
    out = edit_file(ctx(config), "big.k", "210000.0", "200000.0")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert "Edited" in out and big.stat().st_size == size
    assert peak < 8 * 1024 * 1024, f"peak {peak} bytes for a {size} byte file"
    with open(big, "rb") as stream:
        stream.seek(size - 40)
        assert stream.read().endswith(b"1 7.85e-9 200000.0 0.3\n*END\n")


def test_full_backup_keeps_a_copy(setup):
    config, work, _ = setup
    config.data["edit"]["full_backup"] = True
    context = ctx(config)
    edit_file(context, "main.k", "*END", "*END\n")
    copies = [p for p in (work / ".champion" / "backups" / context.audit.session_id).iterdir()
              if p.name.endswith("-main.k")]
    assert len(copies) == 1 and copies[0].read_text() == MODEL


# ------------------------------------------------------------------ files_organize

def test_organize_creates_moves_and_undoes_as_one_group(setup):
    config, work, _ = setup
    (work / "a.k").write_text("a")
    (work / "b.dyn").write_text("b")
    (work / "run1").mkdir()
    context = ctx(config)
    out = organize_files(context, [{"source": "a.k", "destination": "models/"},
                                   {"source": "main.k", "destination": "models"},
                                   {"source": "b.dyn", "destination": "runs/deck.dyn"},
                                   {"source": "run1", "destination": "run1_old"}],
                         create_folders=["models", "runs"])
    assert out.splitlines()[0] == "Plan: 2 folder(s) to create, 4 move(s)"
    assert "Changes #1-#6 in /changes; /undo takes this whole plan back." in out
    assert (work / "models" / "a.k").exists() and (work / "models" / "main.k").exists()
    assert (work / "runs" / "deck.dyn").exists() and (work / "run1_old").is_dir()
    listing = changes_text(context)
    assert "renamed" in listing and "moved" in listing and "created" in listing
    report = undo(context)
    assert report.count("moved back") == 4 and report.count("removed") == 2
    assert sorted(p.name for p in work.iterdir() if p.name != ".champion") == ["a.k", "b.dyn", "main.k", "run1"]


def test_organize_checks_the_whole_plan_first(setup):
    config, work, outside = setup
    (work / "a.k").write_text("a")
    (work / "b.k").write_text("b")
    (work / "taken.k").write_text("keep")
    bad_plans = [
        [{"source": "a.k", "destination": "x.k"}, {"source": "b.k", "destination": "taken.k"}],
        [{"source": "a.k", "destination": "same.k"}, {"source": "b.k", "destination": "same.k"}],
        [{"source": "a.k", "destination": "missing/a.k"}],
        [{"source": "nothing.k", "destination": "n.k"}],
        [{"source": "a.k", "destination": str(outside / "a.k")}],
        [{"source": "a.k", "destination": "secrets/a.k"}],
        [{"source": ".", "destination": "elsewhere"}],
    ]
    for plan in bad_plans:
        with pytest.raises(ToolError, match="nothing was changed"):
            organize_files(ctx(config), plan, create_folders=["new_folder"])
    (work / "tree").mkdir()
    with pytest.raises(ToolError, match="into itself"):
        organize_files(ctx(config), [{"source": "tree", "destination": "tree/sub/"}], create_folders=["tree/sub"])
    with pytest.raises(PolicyDenied):
        organize_files(ctx(config, "1.1"), [{"source": "a.k", "destination": "c.k"}])
    assert sorted(p.name for p in work.iterdir()) == ["a.k", "b.k", "main.k", "taken.k", "tree"]
    assert (work / "taken.k").read_text() == "keep"


def test_organize_failure_midway_reports_and_undo_takes_back(setup, monkeypatch):
    config, work, _ = setup
    for name in ("a.k", "b.k", "c.k"):
        (work / name).write_text(name)
    real = os.rename
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError(5, "Input/output error")
        real(src, dst)
    monkeypatch.setattr(os, "rename", flaky)
    context = ctx(config)
    out = organize_files(context, [{"source": n, "destination": "old/"} for n in ("a.k", "b.k", "c.k")],
                         create_folders=["old"])
    assert "STOPPED: step 3 failed: Input/output error; 2 step(s) done, 2 not done." in out
    monkeypatch.setattr(os, "rename", real)
    assert (work / "old" / "a.k").exists() and (work / "b.k").exists()
    undo(context)
    assert sorted(p.name for p in work.iterdir() if p.name != ".champion") == ["a.k", "b.k", "c.k", "main.k"]


def test_undo_single_entry_and_level_rules(setup):
    config, work, _ = setup
    audit = AuditLog()
    context = ctx(config, audit=audit)
    edit_file(context, "main.k", "*END", "*END\n$ one")
    organize_files(context, [{"source": "main.k", "destination": "model.k"}])
    with pytest.raises(ToolError, match="there is no change #9"):
        undo(context, 9)
    read_only = ctx(config, "1.1", audit=audit)
    with pytest.raises(PolicyDenied, match="does not allow writing"):
        undo(read_only)
    assert "moved back" in undo(context, 2)
    assert "restored" in undo(context, 1)
    assert (work / "main.k").read_text() == MODEL
    with pytest.raises(ToolError, match="already undone"):
        undo(context, 1)
    with pytest.raises(ToolError, match="is itself an undo"):
        undo(context, 3)
    audit.record_change(tool="shell_run", action="copied", path=str(work / "x"))
    with pytest.raises(ToolError, match="cannot be undone"):
        undo(context)


# ------------------------------------------------------------------ as tools

def test_edit_tools_through_the_guard(setup):
    config, work, _ = setup
    model = FakeModel([tool_call("file_edit", {"path": "main.k", "old_text": "*END", "new_text": "*END\n"},
                                 call_id="c1"),
                       tool_call("files_organize", {"moves": [{"source": "main.k", "destination": "m.k"}],
                                                    "create_folders": None}, call_id="c2"),
                       text("done")])
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(), level="1.2",
                              agent_factory=lambda m: Agent(name="Champion", tools=EDIT_TOOLS, model=m))
    asyncio.run(runtime.ask("edit"))
    rows = runtime.audit.recent()
    assert [(r["tool"], r["decision"], r["agent"]) for r in rows] == [("file_edit", "allow", "Champion"),
                                                                    ("files_organize", "allow", "Champion")]
    assert "*END" not in rows[0]["arguments"]  # texts are not stored in the audit, only their size
    changes = runtime.audit.session_changes()
    assert [(c["tool"], c["agent"]) for c in changes] == [("file_edit", "Champion"), ("files_organize", "Champion")]
    assert (work / "m.k").exists()


def test_edit_tools_hidden_at_read_only(setup):
    config, _, _ = setup
    model = FakeModel([text("ok")])
    runtime = ChampionRuntime(config, model=model, audit=AuditLog(), level="1.1",
                              agent_factory=lambda m: Agent(name="Champion", tools=EDIT_TOOLS, model=m))
    asyncio.run(runtime.ask("edit"))
    assert model.calls[0]["tools"] == []
