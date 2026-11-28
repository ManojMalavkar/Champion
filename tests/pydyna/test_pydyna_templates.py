"""T1.2: PyDyna Code sub-agent writes and checks scripts (templates.py, pydyna_template, core file tools).

User, 28 Nov 2026: two checked templates (deck skeleton; material, section and part) on the offline
keywords/Deck API; no PyDyna write tool (core file_create / file_write / file_edit and code_check);
check the text before writing and the file after; exact edits for fixes; never run a script.

Tests marked ``real_only`` need the installed ansys-dyna-core (the API check reads it); they are
skipped where it is not installed. Nothing here runs a template: the .k decks are checked by hand
(docs/steps/pydyna-10-code-agent.md).
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import itertools
import json

import pytest
from fake_model import FakeModel, text, tool_call

from champion.chat import chat_loop
from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.core.runtime import ChampionRuntime
from champion.policy.tool_policy import rule_for
from champion.specialists.pydyna import CODE
from champion.specialists.pydyna import api_catalog as ac
from champion.specialists.pydyna import templates as tpl
from champion.specialists.pydyna import tools as pt
from champion.storage.audit import AuditLog

try:
    ac.installed_package()
    REAL = True
except ac.CatalogError:
    REAL = False
real_only = pytest.mark.skipif(not REAL, reason="ansys-dyna-core is not installed")
_ids = itertools.count(1)


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    work.mkdir()
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def check(context, code=None, path=None) -> dict:
    return json.loads(pt.script_check(context, pt.installed_catalog(), path, code))


# ------------------------------------------------------------------ templates (no package needed)

def test_two_templates_and_the_others_wait_for_the_t1_6_skills():
    assert tpl.names() == ["deck_skeleton", "material_section_part"]
    assert tpl.get("Deck skeleton") is tpl.DECK_SKELETON and tpl.get("material-section-part") is tpl.MATERIAL_SECTION_PART
    for later in ("contact", "boundary_condition", "initial_velocity", "load_curve", "prescribed_motion"):
        with pytest.raises(KeyError, match="T1.6"):
            tpl.get(later)


@pytest.mark.parametrize("name", tpl.names())
def test_template_uses_only_the_offline_deck_api_and_writes_a_k_file(name):
    code = tpl.get(name).code
    tree = ast.parse(code)  # compiles
    compile(code, name, "exec")
    modules, from_core = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
            if node.module == "ansys.dyna.core":
                from_core |= {alias.name for alias in node.names}
    assert modules <= {"pathlib", "pandas", "ansys.dyna.core"}  # no pre, run, subprocess, os
    assert from_core == {"Deck", "keywords"}
    assert "DynaMech" not in code and ".pre" not in code and "launch" not in code
    assert 'Path(__file__).with_suffix(".k")' in code and "OUTPUT.write_text(deck.write()" in code
    for keyword in tpl.get(name).keywords:
        assert keyword in tpl.get(name).description


def test_templates_are_immutable_and_pinned_by_sha256(monkeypatch):
    import dataclasses
    import types

    with pytest.raises(TypeError):
        tpl.TEMPLATES["mine"] = tpl.DECK_SKELETON  # read-only mapping
    with pytest.raises(dataclasses.FrozenInstanceError):
        tpl.DECK_SKELETON.code = "x = 1\n"
    for template in tpl.TEMPLATES.values():
        assert tpl.code_sha256(template.code) == template.sha256 and len(template.sha256) == 64
    changed = dataclasses.replace(tpl.DECK_SKELETON, code=tpl.DECK_SKELETON.code + "# edited\n")
    monkeypatch.setattr(tpl, "TEMPLATES", types.MappingProxyType({"deck_skeleton": changed}))
    with pytest.raises(tpl.TemplateChanged, match="recorded SHA-256"):
        tpl.get("deck_skeleton")
    with pytest.raises(pt.ToolError, match="recorded SHA-256"):
        pt.template_result("deck_skeleton")


def test_provenance_and_package_compatibility():
    for template in tpl.TEMPLATES.values():
        assert template.checked_with == ("0.12.1",) and "DynaMech" in template.provenance
        assert "user" in template.provenance and template.api_names
    assert tpl.compatibility("0.12.1") == "checked with the installed ansys-dyna-core 0.12.1"
    assert "not checked with this version" in tpl.compatibility("0.13.0")
    assert "not installed" in tpl.compatibility(None)
    one = json.loads(pt.template_result("material_section_part"))
    assert one["sha256"] == tpl.MATERIAL_SECTION_PART.sha256 and one["provenance"] == tpl.PROVENANCE
    assert one["checked_with"] == ["ansys-dyna-core 0.12.1"] and one["compatibility"]


@real_only
@pytest.mark.parametrize("name", tpl.names())
def test_every_recorded_api_name_is_in_the_installed_package(name):
    catalog = pt.installed_catalog()
    for api_name in tpl.get(name).api_names:
        assert json.loads(pt.api_record(catalog, api_name))  # raises ToolError if unknown


def test_template_tool_lists_and_returns_text_with_right_none(setup):
    config, _ = setup
    assert rule_for("pydyna_template").right == "none"
    listing = json.loads(pt.template_result(None))
    assert [t["name"] for t in listing["templates"]] == tpl.names() and "contact" in listing["not_yet"]
    one = json.loads(pt.template_result("deck_skeleton"))
    assert one["code"] == tpl.DECK_SKELETON.code and "no DynaMech" in one["api"] and "Never run" in one["note"]
    with pytest.raises(pt.ToolError, match="T1.6"):
        pt.template_result("contact")


def test_code_sub_agent_writes_with_core_tools_and_cannot_run_anything():
    names = CODE.tool_names
    assert {"file_create", "file_write", "file_edit", "code_check", "pydyna_template"} <= set(names)
    assert {rule_for(n).right for n in names} <= {"none", "read", "edit"}  # nothing that runs
    assert not {"shell_run", "shell_pipeline", "run_check", "program_run"} & set(names)
    assert not any(n.startswith("program_") or "write_pydyna" in n for n in names)
    rules = CODE.instructions
    for phrase in ("DynaMech", "check_pydyna_script (code=...) on the complete text before writing",
                   "code_check (paths=[the file]) and check_pydyna_script (path=the file)",
                   "file_edit, exact old_text -> new_text", "three unsuccessful correction attempts",
                   "Never run the script"):
        assert phrase in rules


# ------------------------------------------------------------------ with the installed package

@real_only
@pytest.mark.parametrize("name", tpl.names())
def test_template_passes_the_api_check_without_errors_or_warnings(setup, name):
    config, _ = setup
    result = check(ChampionContext.from_config(config, level="1.1", audit=AuditLog()), code=tpl.get(name).code)
    assert (result["ok"], result["errors"], result["warnings"]) == (True, 0, 0), result["issues"]


@real_only
def test_table_data_given_to_the_constructor_is_a_field_and_a_wrong_name_is_not(setup):
    # found 28 Nov 2026: PyDyna passes the table name positionally (TableCard / TableCardGroup)
    config, _ = setup
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    code = ("import pandas as pd\nfrom ansys.dyna.core import keywords as kwd\n"
            "kwd.Node(nodes=pd.DataFrame({'nid': [1]}))\nkwd.Part(parts=pd.DataFrame({'pid': [1]}))\n"
            "kwd.Part(partz=pd.DataFrame({'pid': [1]}))\n")
    result = check(context, code=code)
    assert [(i["line"], i["code"]) for i in result["issues"]] == [(5, "unknown-field")]
    assert "partz" in result["issues"][0]["message"]


@real_only
@pytest.mark.parametrize("name", ["*PART", "*NODE", "*HOURGLASS", "Part"])
def test_keyword_equal_to_its_subkeyword_has_pydyna_s_title(name):
    # found 28 Nov 2026: Part has keyword PART and subkeyword PART; PyDyna writes *PART, not *PART_PART
    record = json.loads(pt.api_record(pt.installed_catalog(), name))
    assert record["keyword"] in ("*PART", "*NODE", "*HOURGLASS")
    with pytest.raises(pt.ToolError):
        pt.api_record(pt.installed_catalog(), "*PART_PART")


# ------------------------------------------------------------------ the chat flow (scripted model)

def code_turn(task, inner, answer):
    """Orchestrator -> pydyna_agent -> pydyna_code_agent -> inner tool calls -> answers."""
    steps = [tool_call("pydyna_agent", {"input": task}, call_id=f"o{next(_ids)}"),
             tool_call("pydyna_code_agent", {"input": task}, call_id=f"g{next(_ids)}")]
    steps += [tool_call(name, args, call_id=f"c{next(_ids)}") for name, args in inner]
    return steps + [text(answer), text(answer), text(answer)]


def drive(config, outputs, lines, level):
    runtime = ChampionRuntime(config, model=FakeModel(outputs), audit=AuditLog(), level=level)
    queue, out = list(lines), []

    def read_line(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    return runtime, "\n".join(out)


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


@real_only
def test_script_is_checked_written_checked_again_and_never_run_at_1_2(setup):
    config, work = setup
    script = tpl.MATERIAL_SECTION_PART.code
    task = "Write shell_part.py: a deck with one shell part."
    outputs = code_turn(task, [("pydyna_template", {"name": "material_section_part"}),
                               ("check_pydyna_script", {"code": script}),
                               ("file_create", {"path": "shell_part.py", "content": script}),
                               ("code_check", {"paths": ["shell_part.py"]}),
                               ("check_pydyna_script", {"path": "shell_part.py"})], "Wrote shell_part.py.")
    runtime, out = drive(config, outputs, [task, "/changes"], level="1.2")
    assert (work / "shell_part.py").read_text() == script
    checks = [json.loads(o) for o in outputs_of(runtime, "check_pydyna_script")]
    assert [(c["ok"], c["script"]) for c in checks] == [(True, "<code>"), (True, str(work / "shell_part.py"))]
    code_check_out = outputs_of(runtime, "code_check")[0]
    assert "shell_part.py" in code_check_out and "ERROR" not in code_check_out
    assert "Changed files this turn: 1 (see /changes)" in out and " created " in out.split("Changes in this session")[-1]
    assert sorted(p.name for p in work.iterdir()) == ["shell_part.py"]  # no .k deck: the script never ran


def code_tools_offered(runtime):
    return [c["tools"] for c in runtime.model.calls if c["instructions"].startswith("You are PyDyna's Code")]


@pytest.mark.parametrize("level, writes", [("1.1", False), ("1.2", True)])
def test_file_tools_are_offered_only_at_a_level_with_edit_access(setup, level, writes):
    # the core tools' own is_enabled (requires edit): at 1.1 the model never sees them
    config, work = setup
    task = "Which templates are there?"
    runtime, _ = drive(config, code_turn(task, [("pydyna_template", {})], "Two."), [task], level=level)
    offered = code_tools_offered(runtime)[0]
    assert "pydyna_template" in offered and "code_check" in offered and "check_pydyna_script" in offered
    assert all(((name in offered) == writes) for name in ("file_create", "file_edit", "file_write"))
    assert list(work.iterdir()) == []


@real_only
def test_a_warning_in_the_written_file_is_fixed_with_one_exact_edit(setup):
    config, work = setup
    good = tpl.MATERIAL_SECTION_PART.code
    bad = good.replace("etan=1000.0", "etann=1000.0")
    digest = hashlib.sha256(bad.encode("utf-8")).hexdigest()
    task = "Write part.py."
    outputs = code_turn(task, [("file_create", {"path": "part.py", "content": bad}),
                               ("check_pydyna_script", {"path": "part.py"}),
                               ("file_edit", {"path": "part.py", "old_text": "etann=1000.0", "new_text": "etan=1000.0",
                                              "expected_sha256": digest}),
                               ("check_pydyna_script", {"path": "part.py"})], "Fixed etann.")
    runtime, _ = drive(config, outputs, [task], level="1.2")
    first, second = [json.loads(o) for o in outputs_of(runtime, "check_pydyna_script")]
    assert [i["code"] for i in first["issues"]] == ["unknown-field"] and "etann" in first["issues"][0]["message"]
    assert second["ok"] and (work / "part.py").read_text() == good
