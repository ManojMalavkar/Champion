"""T8.2 - L1 working memory and selection of earlier turns."""

import copy

import pytest

from champion.memory.storage import MemoryRefused
from champion.memory.working import (WorkingMemory, WorkingSettings, estimate_tokens, find_entities,
                                     make_session_callback, select_history, split_turns)


def turn(n, output="ok"):
    return [{"role": "user", "content": f"question {n}"},
            {"type": "function_call", "call_id": f"c{n}", "name": "filesystem_read", "arguments": "{}"},
            {"type": "function_call_output", "call_id": f"c{n}", "output": output},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": f"answer {n}"}]}]


def history(count, output="ok"):
    return [item for n in range(count) for item in turn(n, output)]


NEW = [{"role": "user", "content": "next"}]


def test_keeps_last_turns_and_reports_dropped():
    selection = select_history(history(12), NEW, WorkingSettings(max_turns=8))
    assert selection.kept_turns == 8 and len(selection.dropped_turns) == 4
    assert selection.items[0]["content"] == "question 4" and selection.items[-1] == NEW[0]
    assert selection.dropped_turns[0][0]["content"] == "question 0"


def test_older_tool_outputs_cut_recent_kept_and_input_unchanged():
    original = history(4, output="x" * 9000)
    before = copy.deepcopy(original)
    selection = select_history(original, NEW, WorkingSettings(max_turns=8, max_tokens=400000,
                                                              tool_output_chars=500, full_tool_turns=2))
    outputs = [i["output"] for i in selection.items if i.get("type") == "function_call_output"]
    assert [len(o) < 1000 for o in outputs] == [True, True, False, False]
    assert "call the tool again" in outputs[0]
    assert selection.cut_outputs == 2 and original == before


def test_token_budget_drops_oldest_and_never_splits_call_from_result():
    selection = select_history(history(8, output="y" * 4000), NEW,
                               WorkingSettings(max_turns=8, max_tokens=3000, full_tool_turns=8))
    assert selection.estimated_tokens <= 3000 and selection.dropped_turns
    calls = {i["call_id"] for i in selection.items if i.get("type") == "function_call"}
    results = {i["call_id"] for i in selection.items if i.get("type") == "function_call_output"}
    assert calls == results
    for kept in split_turns(selection.items[:-1]):
        assert kept[0]["role"] == "user"


def test_single_huge_turn_cut_then_dropped():
    huge = turn(0, output="z" * 200000)
    fits = select_history(huge, NEW, WorkingSettings(max_tokens=1000, tool_output_chars=200))
    assert fits.kept_turns == 1 and fits.cut_outputs == 1 and estimate_tokens(fits.items) <= 1000
    long_question = [dict(huge[0], content="q" * 20000)] + huge[1:]  # the user text itself is too long
    selection = select_history(long_question, NEW, WorkingSettings(max_tokens=1000, tool_output_chars=200))
    assert selection.items == NEW and len(selection.dropped_turns) == 1
    assert selection.dropped_turns[0][2]["output"] == "z" * 200000  # L2 gets the original turn


def test_session_callback_reports_dropped_turns():
    seen = []
    callback = make_session_callback(WorkingSettings(max_turns=2), on_dropped=seen.append)
    items = callback(history(5), NEW)
    assert items[0]["content"] == "question 3" and len(seen[0]) == 3


def test_settings_checked():
    for bad in ({"max_turns": 0}, {"max_tokens": 10}, {"max_turns": True}, {"tool_output_chars": "5"}):
        with pytest.raises(MemoryRefused):
            WorkingSettings(**bad)


def test_find_entities():
    found = find_entities("Check parts 210045 and 210046 in /models/R07/frontal/main.k, *MAT_024:120045, "
                          "job 784231, material 120045, model M154. Speed in km/h and/or mm.")
    assert found == {"paths": ["/models/R07/frontal/main.k"], "keyword_ids": ["*MAT_024:120045"],
                     "part_ids": ["210045", "210046"], "material_ids": ["120045"], "job_ids": ["784231"],
                     "model_ids": ["M154"]}


def test_working_memory_items_render_and_clear():
    memory = WorkingMemory(session_id="s1")
    assert memory.render() == ""
    memory.set_task("Increase rail energy absorption")
    memory.pin_instruction("Use /models/R07/frontal/main.k as the master file")
    memory.add_correction("No, parts 210045 and 210046 only")
    memory.add_assumption("Units are mm-ms-kg-kN")
    memory.add_question("Which load case?")
    memory.add_pending_approval("SIGY 340 -> 360 in *MAT_024:120045")
    memory.add_evidence("/models/R07/frontal/materials.k#L120", "*MAT_024 ...")
    memory.add_entity("job_ids", "784231")
    assert memory.selected_entities["paths"] == ["/models/R07/frontal/main.k"]
    assert memory.selected_entities["part_ids"] == ["210045", "210046"]
    text = memory.render()
    for expected in ("Active task: Increase rail", "User corrections", "Pending approvals", "Which load case?",
                     "job_ids: 784231", "materials.k#L120", "Assumptions (not confirmed)"):
        assert expected in text
    assert len(memory.render(max_chars=600)) <= 600
    copy_ = WorkingMemory.from_dict(memory.to_dict())
    assert copy_ == memory
    memory.resolve_question("Which load case?")
    assert memory.open_questions == []
    memory.clear_task()
    assert memory.render() == "" and memory.session_id == "s1"


def test_working_memory_refuses_secrets_and_bad_kinds():
    memory = WorkingMemory(session_id="s1")
    with pytest.raises(MemoryRefused, match="secrets are never saved"):
        memory.pin_instruction("use token=abcd1234efgh for the gateway")
    with pytest.raises(MemoryRefused, match="unknown entity kind"):
        memory.add_entity("passwords", "x")
    with pytest.raises(MemoryRefused, match="empty"):
        memory.add_assumption("  ")
    for n in range(60):
        memory.add_question(f"q{n}")
    assert len(memory.open_questions) == 50 and memory.open_questions[-1] == "q59"
