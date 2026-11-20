"""Step 4d.1 + 4d.2: file_edit with a list of edits (replace, delete, insert_before,
insert_after, replace_lines), one atomic change, one diff, one /undo; overlap and
all-or-nothing checks; expected_sha256, dry_run and approval of higher-risk edits.
"""

import hashlib
import json
import os

import pytest

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.policy.scope import PolicyDenied
from champion.storage.audit import AuditLog
from champion.tools import text_patch
from champion.tools.changes import changes_text, undo
from champion.tools.edit import EditOperation, edit_file, edit_file_ops, file_edit
from champion.tools.guard import ToolError

CODE = ("import os\nimport pathlib\n\nTIMEOUT = 10\nLEGACY_MODE = True\n\n\n"
        "def run():\n    return os.getcwd()\n")


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    (work / "app.py").write_text(CODE)
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def ctx(config, level="1.2", approver=None):
    context = ChampionContext.from_config(config, level=level, audit=AuditLog())
    context.approver = approver
    return context


def count_changes(context):
    return changes_text(context).count("file_edit]")


def test_several_edits_are_one_change_with_one_diff_and_one_undo(setup):
    config, work = setup
    context = ctx(config)
    out = edit_file_ops(context, "app.py", [
        {"operation": "insert_after", "anchor": "import pathlib\n", "text": "import logging\n"},
        {"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"},
        {"operation": "delete", "old_text": "LEGACY_MODE = True\n"},
        {"operation": "insert_before", "anchor": "def run():", "text": "def validate():\n    return True\n\n\n"},
    ])
    expected = ("import os\nimport pathlib\nimport logging\n\nTIMEOUT = 30\n\n\n"
                "def validate():\n    return True\n\n\ndef run():\n    return os.getcwd()\n")
    assert (work / "app.py").read_text() == expected
    assert out.startswith(f"Edited {work / 'app.py'}: 4 edits in one change (line 3: 1 line(s) inserted after")
    assert "Change #1 in /changes; /undo restores it." in out
    assert out.count("--- ") == 1 and "+ import logging" in out and "- LEGACY_MODE = True" in out
    digest = hashlib.sha256((work / "app.py").read_bytes()).hexdigest()
    assert f"File SHA-256 now {digest}." in out
    assert count_changes(context) == 1 and "4 edits: line 3" in changes_text(context)
    assert "restored" in undo(context)
    assert (work / "app.py").read_text() == CODE


def test_any_failing_edit_changes_nothing(setup):
    config, work = setup
    context = ctx(config)
    with pytest.raises(ToolError, match=r"edit 2 \(insert_after\): anchor was not found"):
        edit_file_ops(context, "app.py", [
            {"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"},
            {"operation": "insert_after", "anchor": "import missing\n", "text": "x\n"}])
    with pytest.raises(ToolError, match="edits 1 and 2 overlap"):
        edit_file_ops(context, "app.py", [
            {"operation": "replace", "old_text": "TIMEOUT = 10\nLEGACY", "new_text": "T"},
            {"operation": "delete", "old_text": "LEGACY_MODE = True\n"}])
    with pytest.raises(ToolError, match="insert at the same place"):
        edit_file_ops(context, "app.py", [
            {"operation": "insert_before", "anchor": "def run", "text": "# a\n"},
            {"operation": "insert_before", "anchor": "def run", "text": "# b\n"}])
    # touching blocks are fine: replace ends where the insert starts
    edit_file_ops(context, "app.py", [
        {"operation": "replace", "old_text": "import os\n", "new_text": "import sys\n"},
        {"operation": "insert_before", "anchor": "import pathlib", "text": "import re\n"}])
    assert (work / "app.py").read_text().startswith("import sys\nimport re\nimport pathlib\n")
    assert count_changes(context) == 1


def test_fields_are_checked_per_operation(setup):
    config, work = setup
    context = ctx(config)
    cases = [
        ({"operation": "replace", "old_text": "TIMEOUT", "new_text": "T", "anchor": "x"}, "does not take anchor"),
        ({"operation": "rename", "old_text": "a"}, "operations: replace"),
        ({"operation": "replace", "old_text": "TIMEOUT"}, "needs new_text"),
        ({"operation": "insert_after", "anchor": "run", "text": ""}, "text must not be empty"),
        ({"operation": "insert_after", "anchor": "", "text": "x"}, "anchor must not be empty"),
        ({"operation": "replace_lines", "start_line": 4, "end_line": 4, "new_text": "x"}, "expected_text"),
        ({"operation": "replace_lines", "start_line": 5, "end_line": 4, "expected_text": "x", "new_text": "y"},
         "1 <= start_line <= end_line"),
        ({"operation": "replace", "old_text": "import", "new_text": "x", "occurrence": 0}, "between 1 and"),
    ]
    for edit, message in cases:
        with pytest.raises(ToolError, match=message):
            edit_file_ops(context, "app.py", [edit])
    with pytest.raises(ToolError, match="at most 20 edits"):
        edit_file_ops(context, "app.py", [{"operation": "delete", "old_text": "x"}] * 21)
    with pytest.raises(ToolError, match="non-empty list"):
        edit_file_ops(context, "app.py", [])
    assert (work / "app.py").read_text() == CODE and count_changes(context) == 0


def test_occurrence_and_idempotent_insert(setup):
    config, work = setup
    context = ctx(config)
    with pytest.raises(ToolError, match=r"matches 2 times .* \(first at lines 1, 2\).*give occurrence"):
        edit_file_ops(context, "app.py", [{"operation": "replace", "old_text": "import ", "new_text": "from "}])
    with pytest.raises(ToolError, match="occurrence 3 does not exist"):
        edit_file_ops(context, "app.py", [{"operation": "replace", "old_text": "import ", "new_text": "x",
                                           "occurrence": 3}])
    edit_file_ops(context, "app.py", [{"operation": "replace", "old_text": "import ", "new_text": "import  ",
                                       "occurrence": 2}])
    assert (work / "app.py").read_text().startswith("import os\nimport  pathlib\n")
    add = {"operation": "insert_after", "anchor": "import os\n", "text": "import re\n", "idempotent": True}
    edit_file_ops(context, "app.py", [add])
    again = edit_file_ops(context, "app.py", [add])
    assert again.startswith("Nothing changed") and "already in the file" in again
    assert (work / "app.py").read_text().count("import re\n") == 1 and count_changes(context) == 2


def test_replace_lines_checks_expected_text_and_keeps_line_breaks(setup):
    config, work = setup
    context = ctx(config)
    with pytest.raises(ToolError, match="lines 4-5 of .* are not expected_text"):
        edit_file_ops(context, "app.py", [{"operation": "replace_lines", "start_line": 4, "end_line": 5,
                                           "expected_text": "TIMEOUT = 99\nLEGACY_MODE = True", "new_text": "x"}])
    with pytest.raises(ToolError, match=r"lines 20-21 are not in .* \(it has 9 line\(s\)\)"):
        edit_file_ops(context, "app.py", [{"operation": "replace_lines", "start_line": 20, "end_line": 21,
                                           "expected_text": "x", "new_text": "y"}])
    # expected_text with or without the final newline; new_text without one keeps the line break
    edit_file_ops(context, "app.py", [{"operation": "replace_lines", "start_line": 4, "end_line": 5,
                                       "expected_text": "TIMEOUT = 10\nLEGACY_MODE = True", "new_text": "TIMEOUT = 5"}])
    assert "\nTIMEOUT = 5\n\n\ndef run" in (work / "app.py").read_text()
    # last line without a newline, then delete lines with an empty new_text
    (work / "tail.txt").write_text("a\nb\nc")
    edit_file_ops(context, "tail.txt", [{"operation": "replace_lines", "start_line": 3, "end_line": 3,
                                        "expected_text": "c", "new_text": "C"}])
    assert (work / "tail.txt").read_text() == "a\nb\nC"
    out = edit_file_ops(context, "tail.txt", [{"operation": "replace_lines", "start_line": 1, "end_line": 2,
                                              "expected_text": "a\nb\n", "new_text": ""}])
    assert (work / "tail.txt").read_text() == "C" and "lines 1-2: 2 line(s) deleted" in out
    assert "restored" in undo(context) and (work / "tail.txt").read_text() == "a\nb\nC"


def test_line_range_across_chunk_borders(setup, monkeypatch):
    config, work = setup
    monkeypatch.setattr(text_patch, "CHUNK", 5)
    data = "".join(f"line {n}\n" for n in range(1, 31))
    (work / "lines.txt").write_text(data)
    fd = os.open(work / "lines.txt", os.O_RDONLY)
    try:
        a, b, _ = text_patch.line_range(fd, 12, 14, float("inf"))
        assert data.encode()[a:b] == b"line 12\nline 13\nline 14\n"
        assert text_patch.line_range(fd, 1, 1, float("inf"))[:2] == (0, 7)
        assert text_patch.line_range(fd, 30, 31, float("inf")) == (-1, -1, 30)
    finally:
        os.close(fd)
    edit_file_ops(ctx(config), "lines.txt", [
        {"operation": "replace_lines", "start_line": 12, "end_line": 14,
         "expected_text": "line 12\nline 13\nline 14\n", "new_text": "twelve\n"},
        {"operation": "insert_after", "anchor": "line 29\n", "text": "line 29b\n"}])
    text = (work / "lines.txt").read_text()
    assert "line 11\ntwelve\nline 15\n" in text and "line 29\nline 29b\nline 30\n" in text


def test_undo_of_edits_that_grow_and_shrink(setup):
    config, work = setup
    context = ctx(config)
    edit_file_ops(context, "app.py", [
        {"operation": "insert_before", "anchor": "import os", "text": "# header\n" * 5},
        {"operation": "replace", "old_text": "TIMEOUT = 10\nLEGACY_MODE = True\n", "new_text": "T = 1\n"},
        {"operation": "insert_after", "anchor": "return os.getcwd()\n", "text": "# end\n"}])
    assert (work / "app.py").read_text().count("# header") == 5
    undo(context)
    assert (work / "app.py").read_text() == CODE
    edit_file_ops(context, "app.py", [{"operation": "delete", "old_text": "import pathlib\n"}])
    with open(work / "app.py", "a") as stream:
        stream.write("# typed by the user\n")
    assert "NOT undone" in undo(context)


def test_crlf_and_latin1_with_several_edits(setup):
    config, work = setup
    (work / "dos.k").write_bytes(b"*KEYWORD\r\n*PART\r\n1\r\n*END\r\n")
    edit_file_ops(ctx(config), "dos.k", [
        {"operation": "insert_after", "anchor": "*KEYWORD\n", "text": "$ comment\n"},
        {"operation": "replace_lines", "start_line": 3, "end_line": 3, "expected_text": "1", "new_text": "2"}])
    assert (work / "dos.k").read_bytes() == b"*KEYWORD\r\n$ comment\r\n*PART\r\n2\r\n*END\r\n"
    (work / "latin.k").write_bytes("*KEYWORD\n$ M\u00fcller\n*END\n".encode("latin-1"))
    edit_file_ops(ctx(config), "latin.k", [{"operation": "insert_before", "anchor": "*END", "text": "$ Gr\u00f6\u00dfe\n"}])
    assert (work / "latin.k").read_bytes() == "*KEYWORD\n$ M\u00fcller\n$ Gr\u00f6\u00dfe\n*END\n".encode("latin-1")


def test_expected_sha256_and_dry_run(setup):
    config, work = setup
    context = ctx(config)
    digest = hashlib.sha256(CODE.encode()).hexdigest()
    edit = [{"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"}]
    dry = edit_file_ops(context, "app.py", edit, dry_run=True)
    assert dry.startswith("Dry run, nothing changed:") and "+ TIMEOUT = 30" in dry
    assert (work / "app.py").read_text() == CODE and count_changes(context) == 0
    with pytest.raises(ToolError, match="changed since it was read"):
        edit_file_ops(context, "app.py", edit, expected_sha256="0" * 64)
    with pytest.raises(ToolError, match="64-character SHA-256"):
        edit_file_ops(context, "app.py", edit, expected_sha256="abc")
    out = edit_file_ops(context, "app.py", edit, expected_sha256=digest.upper())
    assert "TIMEOUT = 30" in (work / "app.py").read_text() and "Change #1" in out


def test_higher_risk_edits_ask_once(setup):
    config, work = setup
    lines = "".join(f"value {n}\n" for n in range(1, 201))
    (work / "long.txt").write_text(lines)
    block = "".join(f"value {n}\n" for n in range(20, 171))  # 151 lines
    edit = [{"operation": "delete", "old_text": block}]
    with pytest.raises(PolicyDenied, match="needs the user's approval"):
        edit_file_ops(ctx(config), "long.txt", edit)  # no approver (-q, tests)
    asked = []
    with pytest.raises(PolicyDenied, match="did not approve"):
        edit_file_ops(ctx(config, approver=lambda r: asked.append(r) or False), "long.txt", edit)
    assert (work / "long.txt").read_text() == lines
    edit_file_ops(ctx(config, approver=lambda r: asked.append(r) or True), "long.txt", edit)
    assert "value 19\nvalue 171\n" in (work / "long.txt").read_text()
    request = asked[-1]
    assert request.command_class == "edit" and "deletes 151 lines" in request.note
    assert "change  : deletes 151 lines" in request.describe()
    # 20 % rule only for files of at least edit.approval_min_bytes (8 KiB)
    (work / "big.txt").write_text("x" * 9000 + "\nEND\n")
    with pytest.raises(PolicyDenied, match="needs the user's approval"):
        edit_file_ops(ctx(config), "big.txt", [{"operation": "insert_before", "anchor": "END",
                                                "text": "y" * 3000}])  # 3000 of 9005 bytes: 33 %
    edit_file_ops(ctx(config), "app.py", [{"operation": "replace", "old_text": CODE, "new_text": "print(1)\n"}])
    assert (work / "app.py").read_text() == "print(1)\n"  # small file, 9 lines: no prompt
    dry = edit_file_ops(ctx(config), "big.txt", [{"operation": "insert_before", "anchor": "END",
                                                  "text": "y" * 3000}], dry_run=True)
    assert dry.startswith("Dry run") and "y" not in (work / "big.txt").read_text()  # a dry run never asks


def test_old_call_and_tool_schema(setup):
    config, work = setup
    out = edit_file(ctx(config), "app.py", "TIMEOUT = 10", "TIMEOUT = 20")
    assert out.startswith(f"Edited {work / 'app.py'} at line 4: 1 line(s) replaced by 1 line(s)")
    schema = file_edit.params_json_schema
    assert {"path", "edits", "old_text", "new_text", "expected_sha256", "dry_run"} <= set(schema["properties"])
    operation = schema["$defs"]["EditOperation"]
    assert operation["properties"]["operation"]["enum"] == ["replace", "insert_before", "insert_after", "delete",
                                                             "replace_lines"]
    assert set(operation["required"]) == set(operation["properties"])  # strict schema: all keys present
    assert EditOperation(operation="delete", old_text="x").anchor is None


def test_through_the_linux_specialist_and_audit_has_no_text(setup):
    from test_linux_specialist import drive, linux

    config, work = setup
    edits = [{"operation": "insert_after", "anchor": "import pathlib\n", "text": "import logging\n"},
             {"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"}]
    outputs = linux("Add import logging after import pathlib and set TIMEOUT to 30 in app.py",
                    [("filesystem_read", {"path": "app.py"}),
                     ("file_edit", {"path": "app.py", "edits": edits,
                                    "expected_sha256": hashlib.sha256(CODE.encode()).hexdigest()})])
    runtime, out, _ = drive(config, outputs, ["Add logging and set TIMEOUT to 30 in app.py", "/changes", "/undo"],
                            level="1.2")
    assert "[Linux] -> file_edit(" in out and "Changed files this turn: 1" in out
    assert "#1" in out and "restored" in out and (work / "app.py").read_text() == CODE
    rows = [r for r in runtime.audit.recent(20) if r["tool"] == "file_edit"]
    assert rows and rows[0]["decision"] == "allow"
    stored = json.dumps(rows)
    assert "import logging" not in stored and "insert_after" in stored
