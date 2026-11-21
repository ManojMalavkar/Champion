"""T8.3 - L2 conversation summaries (fake model, no network)."""

import asyncio
import json
import stat

import pytest

from champion.memory.storage import MemoryRefused
from champion.memory.summaries import (SUMMARY_INSTRUCTIONS, ConversationSummary, SummaryItem, SummaryStore,
                                       parse_summary, summaries_folder, summarize, turns_to_text)

MODEL_ANSWER = {
    "topic": "PyDyna execution policy",
    "decisions": [{"text": "Generated PyDyna scripts may be written but not run automatically",
                   "reason": "Execution requires controlled Python backend and approval"},
                  {"text": "Increase the rail gauge from 1.6 mm to 1.8 mm"}],
    "constraints": [{"text": "Section force must remain below 320 kN"}],
    "paths": [{"text": "Knowledge DBs live in knowledge/pydyna"}],
    "open_items": [{"text": "Implement approved run-folder execution"}],
    "rejected_alternatives": [{"text": "Run scripts automatically", "reason": "no sandbox yet"}],
}


def fake_model(answer):
    calls = []

    async def call(instructions, text):
        calls.append((instructions, text))
        return answer
    call.calls = calls
    return call


TURNS = [[{"role": "user", "content": "Decide: scripts written but not run. My key is sk-abcdefghijklmnop1234"},
          {"type": "function_call", "name": "filesystem_read", "arguments": "{\"path\": \"/a\"}", "call_id": "1"},
          {"type": "function_call_output", "call_id": "1", "output": "o" * 2000},
          {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Agreed."}]}]]


def test_summarize_with_fake_model_defers_engineering_statements(tmp_path):
    model = fake_model("```json\n" + json.dumps(MODEL_ANSWER) + "\n```")
    summary = asyncio.run(summarize(TURNS, model, project_id="CHAMPION", source_session="SESSION-154"))
    instructions, text = model.calls[0]
    assert instructions == SUMMARY_INSTRUCTIONS
    assert "sk-abc" not in text and "Agreed." in text and "tool call filesystem_read" in text
    kinds = [(item.kind, item.text) for item in summary.items]
    assert ("decision", "Generated PyDyna scripts may be written but not run automatically") in kinds
    assert len(kinds) == 4
    assert {d["layer"] for d in summary.deferred} == {"engineering_fact", "engineering_change"}
    stored = SummaryStore(tmp_path).add(summary)
    assert stored.summary_id.startswith("SUM-")
    data = json.loads((tmp_path / f"{stored.summary_id}.json").read_text())
    assert data["project_id"] == "CHAMPION" and data["source_sessions"] == ["SESSION-154"]
    assert data["decisions"][0]["reason"].startswith("Execution requires") and data["decisions"][0]["status"] == "active"
    assert stat.S_IMODE((tmp_path / f"{stored.summary_id}.json").stat().st_mode) == 0o600


def test_model_answers_refused():
    for bad, message in (("not json", "not valid JSON"), ("[1, 2]", "not a JSON object"),
                         (json.dumps({"topic": "t", "decisions": [{"text": "password=hunter22"}]}), "secrets"),
                         (json.dumps({"topic": "t", "decisions": [{"text": "x", "status": "superseded"}]}),
                          "may not set an item status"),
                         (json.dumps({"topic": "t", "decisions": [{"text": "x"}], "unknown": 1,
                                      "rules": [{"text": ""}]}), "empty")):
        with pytest.raises(MemoryRefused, match=message):
            parse_summary(bad)
    with pytest.raises(MemoryRefused, match="nothing to summarise"):
        asyncio.run(summarize([], fake_model("{}")))


def test_user_format_with_decision_key_and_model_cannot_set_ids():
    answer = json.dumps({"topic": "t", "summary_id": "SUM-19990101-001", "created_at": "1999",
                         "deferred": [{"text": "x"}],
                         "decisions": [{"decision": "Use agents as tools", "reason": "no handoffs"}]})
    summary = parse_summary(answer, project_id="P")
    assert summary.items[0].text == "Use agents as tools" and summary.summary_id == "" and summary.deferred == []
    assert summary.created_at != "1999"


def add(store, text, project="CHAMPION", kind="decision"):
    return store.add(ConversationSummary(topic="t", project_id=project, items=[SummaryItem(kind, text)]))


def test_active_items_supersede_and_project_filter(tmp_path):
    store = SummaryStore(tmp_path)
    first = add(store, "History is 10 turns")
    add(store, "Other project rule", project="OTHER", kind="rule")
    newer = store.supersede(first.summary_id, 0, ConversationSummary(
        topic="t", project_id="CHAMPION", items=[SummaryItem("decision", "History is 8 turns", "token budget")]))
    active = [item.text for _, _, item in store.active_items(project_id="CHAMPION")]
    assert active == ["History is 8 turns"]
    old = store.load(first.summary_id).items[0]
    assert old.status == "superseded" and old.superseded_by == f"{newer.summary_id}#0"
    assert [i.text for _, _, i in store.active_items(kind="rule")] == ["Other project rule"]
    text = store.render_active("CHAMPION")
    assert "History is 8 turns (reason: token budget)" in text and "10 turns" not in text and "Other" not in text
    store.set_status(newer.summary_id, 0, "done")
    assert store.render_active("CHAMPION") == ""


def test_store_refusals(tmp_path):
    store = SummaryStore(tmp_path)
    with pytest.raises(MemoryRefused, match="no items"):
        store.add(ConversationSummary(topic="t"))
    with pytest.raises(MemoryRefused, match="unknown summary item kind"):
        store.add(ConversationSummary(topic="t", items=[SummaryItem("fact", "x")]))
    with pytest.raises(MemoryRefused, match="not a summary ID"):
        store.load("../x")
    stored = add(store, "x")
    with pytest.raises(MemoryRefused, match="no item 3"):
        store.set_status(stored.summary_id, 3, "done")
    with pytest.raises(MemoryRefused, match="unknown status"):
        store.set_status(stored.summary_id, 0, "approved")
    assert list(tmp_path.glob("SUM-*.json")) == [tmp_path / f"{stored.summary_id}.json"]


def test_folders_and_text_conversion(tmp_path):
    assert summaries_folder(tmp_path / "proj") == tmp_path / "proj" / "summaries"
    assert summaries_folder().parts[-3:] == (".champion", "memory", "summaries")
    text = turns_to_text(TURNS, max_chars=100)
    assert text.startswith("[start shortened]") and len(text) <= 120
