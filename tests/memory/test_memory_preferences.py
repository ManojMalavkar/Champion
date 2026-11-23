"""T8.4 - L3 preferences and the separation of memory kinds."""

import json
import stat

import pytest

from champion.memory.preferences import PreferenceStore, check_value, preferences_path
from champion.memory.separation import classify
from champion.memory.storage import MemoryRefused


@pytest.mark.parametrize("text, kind", [
    ("Show force in kN", "preference"),
    ("Section force must remain below 320 kN", "engineering_fact"),
    ("Program target is 300 kN", "requirement"),
    ("Job 784231 produced 318 kN", "result"),
    ("Increase local gauge from 1.6 mm to 1.8 mm", "engineering_change"),
    ("Toe-board intrusion <= 120 mm (Rev_5)", "engineering_fact"),
    ("Generated PyDyna scripts may be written but not run automatically", "preference"),
    ("Use mm-ms-kg-kN", "preference"),
])
def test_separation_of_the_four_kinds(text, kind):
    assert classify(text) == kind


def test_user_example_preferences(tmp_path):
    store = PreferenceStore(tmp_path / "preferences.json", user_id="manoj")
    store.set("unit_system", "mm-ms-kg-kN")
    store.set("default_solver", "LS-DYNA")
    store.set("default_precision", "mpp-double")
    store.set("report_format", "pptx")
    store.set("destructive_action_confirmation", "yes")
    store.set("generated_code_policy", "write_but_do_not_run", source="agent_confirmed")
    store.set("display_units", {"force": "kN"})
    assert store.all() == {"user_id": "manoj", "preferences": {
        "default_precision": "MPP-double", "default_solver": "LS-DYNA", "destructive_action_confirmation": True,
        "display_units": {"force": "kN"}, "generated_code_policy": "write_but_do_not_run",
        "report_format": "pptx", "unit_system": "mm-ms-kg-kN"}}
    assert store.entry("generated_code_policy")["source"] == "agent_confirmed"
    assert stat.S_IMODE((tmp_path / "preferences.json").stat().st_mode) == 0o600
    assert "never change the access rules" in store.render() and "unit_system: mm-ms-kg-kN" in store.render()
    assert store.forget("report_format") is True and store.forget("report_format") is False
    assert store.get("report_format", "pdf") == "pdf"
    reopened = PreferenceStore(tmp_path / "preferences.json")
    assert reopened.get("unit_system") == "mm-ms-kg-kN"


def test_agent_alone_cannot_set(tmp_path):
    store = PreferenceStore(tmp_path / "p.json")
    with pytest.raises(MemoryRefused, match="agent_confirmed"):
        store.set("report_format", "pdf", source="agent")
    assert not (tmp_path / "p.json").exists()


@pytest.mark.parametrize("key, value, message", [
    ("display_units", {"force": "must remain below 320 kN"}, "engineering fact"),
    ("output_naming", "Program target is 300 kN", "requirement"),
    ("report_configuration", "Job 784231 produced 318 kN", "simulation result"),
    ("favourite_colour", "blue", "unknown preference"),
    ("report_format", "docx", "one of"),
    ("unit_system", "metric", "consistent unit system"),
    ("working_locations", ["relative/path"], "not an absolute path"),
    ("hpc_gateway", "user:password=hunter22@host", "secrets are never saved"),
    ("destructive_action_confirmation", False, "cannot turn that off"),
    ("destructive_action_confirmation", "maybe", "true or false"),
    ("generated_code_policy", "run_automatically", "one of"),
    ("tool_aliases", {}, "1 to 50 entries"),
])
def test_refused_values(key, value, message):
    with pytest.raises(MemoryRefused, match=message):
        check_value(key, value)


def test_paths_kept_as_hint_and_deduplicated(tmp_path):
    assert check_value("allowed_project_roots", ["/proj/a", "~/b", "/proj/a"]) == ["/proj/a", "~/b"]
    assert check_value("working_locations", "/nobackup/people/x") == ["/nobackup/people/x"]
    assert preferences_path(tmp_path) == tmp_path / "preferences.json"
    assert preferences_path().parts[-3:] == (".champion", "memory", "preferences.json")


def test_broken_file_refused(tmp_path):
    file = tmp_path / "preferences.json"
    file.write_text("{broken")
    with pytest.raises(MemoryRefused, match="not valid JSON"):
        PreferenceStore(file).get("unit_system")
    file.write_text(json.dumps({"preferences": []}))
    with pytest.raises(MemoryRefused, match="not a preferences file"):
        PreferenceStore(file).all()
