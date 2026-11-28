"""T1.2 guard (guard.py): output paths and the code-enforced correction limit of the Code sub-agent.

User, 28 Nov 2026: only authorized .py output paths; count only failed write/edit-and-validation
cycles, stop after three failed corrections, stop earlier when the code or the validation result
does not change. The chat tests use code_check (no ansys-dyna-core needed), so they run everywhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json

import pytest
from fake_model import FakeModel, text, tool_call

from champion.chat import chat_loop
from champion.core.config import load_config
from champion.core.runtime import ChampionRuntime
from champion.programs.tools import attach_program_tools
from champion.specialists.base import walk_specs
from champion.specialists.pydyna import CODE, PYDYNA
from champion.specialists.pydyna import guard
from champion.storage.audit import AuditLog

_ids = itertools.count(1)
PY, PD = "code_check", "check_pydyna_script"


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def code_turn(task, inner, answer="Done."):
    steps = [tool_call("pydyna_agent", {"input": task}, call_id=f"o{next(_ids)}"),
             tool_call("pydyna_code_agent", {"input": task}, call_id=f"g{next(_ids)}")]
    steps += [tool_call(name, args, call_id=f"c{next(_ids)}") for name, args in inner]
    return steps + [text(answer), text(answer), text(answer)]


def drive(config, outputs, lines, level="1.2"):
    audit = AuditLog()
    runtime = ChampionRuntime(config, model=FakeModel(outputs), audit=audit, level=level)
    queue, out = list(lines), []

    def read_line(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    return runtime, "\n".join(out), audit


def outputs_of(runtime, tool):
    found = []
    for call in runtime.model.calls:
        names = {item.get("call_id"): item.get("name") for item in call["input"]
                 if isinstance(item, dict) and item.get("type") == "function_call"}
        for item in call["input"]:
            if (isinstance(item, dict) and item.get("type") == "function_call_output"
                    and names.get(item["call_id"]) == tool and item["output"] not in found):
                found.append(item["output"])
    return found


def sha(text_: str) -> str:
    return hashlib.sha256(text_.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ path rules (unit)

@pytest.mark.parametrize("name, why", [
    ("deck.k", "only .py"), ("notes.txt", "only .py"), ("build deck.py", "letters, digits"),
    (".hidden.py", "letters, digits"), ("a.b.py", "letters, digits"), ("pandas.py", "instead of the module"),
    ("json.py", "instead of the module"), ("setup.py", "instead of the module"),
    ("conftest.py", "instead of the module"), ("ansys.py", "instead of the module"),
    ("sitecustomize.py", "instead of the module"), ("__init__.py", "letters, digits")])
def test_refused_output_names(tmp_path, name, why):
    assert why in guard.path_problem(tmp_path / name)


def test_accepted_names_and_champion_package_refused(tmp_path):
    for name in ("build_deck.py", "shell_part.py", "Deck-1.py", "part2.py"):
        assert guard.path_problem(tmp_path / name) is None
    inside = guard.PACKAGE_ROOT / "specialists" / "pydyna" / "templates_copy.py"
    assert "Champion's own code" in guard.path_problem(inside)


# ------------------------------------------------------------------ attempt counter (unit)

def test_three_failed_corrections_stop_and_the_first_write_is_not_a_correction():
    state = guard.Attempts()
    state.wrote("v0")
    assert "first version has problems" in state.validated(PY, False, "e0", "x.py")
    for n, (version, problem) in enumerate([("v1", "e1"), ("v2", "e2")], 1):
        state.wrote(version)
        assert f"correction {n} of 3 failed" in state.validated(PY, False, problem, "x.py")
    state.wrote("v3")
    assert state.validated(PY, False, "e3", "x.py").startswith("STOP: x.py: 3 correction attempts failed")
    assert state.stopped and state.corrections == 3


def test_same_problems_after_a_correction_stop_early():
    state = guard.Attempts()
    state.wrote("v0")
    state.validated(PD, False, "same", "x.py")
    state.wrote("v1")
    assert "no progress" in state.validated(PD, False, "same", "x.py") and state.corrections == 1


def test_code_back_to_an_earlier_failed_version_stops_early():
    state = guard.Attempts()
    state.wrote("v0")
    state.validated(PY, False, "e0", "x.py")
    state.wrote("v1")
    state.validated(PY, False, "e1", "x.py")
    state.wrote("v0")  # the correction undid the last one
    assert "left the code as in an earlier failed version" in state.validated(PY, False, "e2", "x.py")


def test_both_checks_passing_reset_and_a_second_failing_check_counts_once():
    state = guard.Attempts()
    state.wrote("v0")
    state.validated(PY, False, "e0", "x.py")
    state.wrote("v1")
    state.validated(PY, False, "e1", "x.py")
    state.validated(PD, False, "p1", "x.py")  # same cycle
    assert state.cycles == 2 and state.corrections == 1
    state.wrote("v2")
    assert state.validated(PY, True, "", "x.py") is None  # one check is not enough
    assert "correction count reset" in state.validated(PD, True, "", "x.py")
    assert state.cycles == 0 and not state.stopped


def test_checks_of_code_text_or_without_a_write_are_not_counted(setup, tmp_path):
    config, work = setup
    context = type("C", (), {"working_directory": work})()
    result = json.dumps({"ok": False, "issues": [{"line": 1, "code": "x", "message": "m"}]})
    assert guard._pydyna_result(context, {"code": "x"}, result) == result
    assert guard._pydyna_result(context, {"path": "x.py"}, result) == result  # no write yet
    assert guard.attempts(context) == {}


# ------------------------------------------------------------------ through the chat (scripted model)

def test_stop_after_three_failed_corrections_then_writes_are_blocked_and_audited(setup):
    config, work = setup
    versions = ["x = (\n", "x = [\n", "x = {\n", "x = 1 +\n"]
    inner = [("file_create", {"path": "x.py", "content": versions[0]}), (PY, {"paths": ["x.py"]})]
    for old, new in zip(versions, versions[1:]):
        inner += [("file_edit", {"path": "x.py", "old_text": old.strip(), "new_text": new.strip(),
                                 "expected_sha256": sha(old)}), (PY, {"paths": ["x.py"]})]
    inner.append(("file_edit", {"path": "x.py", "old_text": "x = 1 +", "new_text": "x = 1",
                                "expected_sha256": sha(versions[-1])}))
    runtime, out, audit = drive(config, code_turn("Write x.py.", inner), ["Write x.py."])
    notes = [o.split("correction_attempts: ")[1] for o in outputs_of(runtime, PY)]
    assert "first version has problems" in notes[0]
    assert "correction 1 of 3 failed" in notes[1] and "correction 2 of 3 failed" in notes[2]
    assert notes[3].startswith("STOP: x.py: 3 correction attempts failed")
    assert outputs_of(runtime, "file_edit")[-1].startswith("BLOCKED: STOP")
    assert (work / "x.py").read_text() == versions[-1]  # the blocked edit changed nothing
    denied = [r for r in audit.recent(50) if r["decision"] == "deny"]
    assert [r["tool"] for r in denied] == ["file_edit"] and "pydyna guard" in denied[0]["reason"]


def test_refused_paths_create_nothing_and_a_good_name_is_written(setup):
    config, work = setup
    inside = str(guard.PACKAGE_ROOT / "specialists" / "pydyna" / "evil.py")
    inner = [("file_create", {"path": name, "content": "x = 1\n"}) for name in ("pandas.py", "deck.k", inside)]
    inner.append(("file_create", {"path": "build_deck.py", "content": "x = 1\n"}))
    runtime, _, audit = drive(config, code_turn("Write scripts.", inner), ["Write scripts."])
    results = outputs_of(runtime, "file_create")
    assert [r.startswith("BLOCKED") for r in results] == [True, True, True, False]
    assert sorted(p.name for p in work.iterdir()) == ["build_deck.py"]
    assert not (guard.PACKAGE_ROOT / "specialists" / "pydyna" / "evil.py").exists()
    assert len([r for r in audit.recent(50) if r["decision"] == "deny" and "pydyna guard" in r["reason"]]) == 3


def test_written_script_is_listed_in_changes_and_undo_removes_it(setup):
    config, work = setup
    inner = [("file_create", {"path": "shell_part.py", "content": "x = 1\n"}), (PY, {"paths": ["shell_part.py"]})]
    _, out, _ = drive(config, code_turn("Write shell_part.py.", inner),
                      ["Write shell_part.py.", "/changes", "/undo", "/changes"])
    first_changes = out.split("Changes in this session")[1]
    assert "created" in first_changes and "shell_part.py" in first_changes
    assert not (work / "shell_part.py").exists()  # /undo removed it
    last_changes = out.split("Changes in this session")[-1]
    assert " undone " in last_changes and "[undone by #2]" in last_changes


# ------------------------------------------------------------------ nothing that runs

def test_code_agent_runs_nothing_and_takes_no_programs(setup):
    config, work = setup
    assert CODE.extra.get("programs") is False
    specs, _ = attach_program_tools(config, [PYDYNA], work)  # no program declared (test default)
    code = next(s for s in walk_specs(specs[0]) if s.name == "pydyna_code")
    assert not any(n.startswith("program") for n in code.tool_names)
    assert not {"shell_run", "shell_pipeline", "run_check", "programs_list", "program_run"} & set(code.tool_names)
    from champion.specialists.pydyna import tools as pt
    from champion.tools.checks import code_check
    from champion.tools.edit import file_create, file_edit, file_write

    core = {"file_create": file_create, "file_edit": file_edit, "file_write": file_write, "code_check": code_check,
            "check_pydyna_script": pt.check_pydyna_script}
    by_name = {t.name: t for t in CODE.tools}
    for name, original in core.items():  # the Code sub-agent has the guarded copies, never the originals
        assert by_name[name] is not original and by_name[name].params_json_schema == original.params_json_schema
