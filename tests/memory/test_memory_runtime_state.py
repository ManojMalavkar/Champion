"""T8.1 - L0 runtime execution state."""

import json
import os
import stat

import pytest

from champion.memory.runtime_state import RuntimeStateStore, runtime_folder
from champion.memory.storage import REDACTED, MemoryRefused


@pytest.fixture
def store(tmp_path):
    return RuntimeStateStore.for_working_directory(tmp_path)


def test_start_save_load_and_file_modes(store, tmp_path):
    state = store.start(agent="pydyna_agent", workflow="modify_material", project_access="read_only",
                        sandbox_path=tmp_path / "sandbox")
    assert state.request_id.startswith("REQ-") and state.current_step == "received"
    file = runtime_folder(tmp_path) / f"{state.request_id}.json"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert stat.S_IMODE(file.parent.stat().st_mode) == 0o700
    second = store.start(agent="linux_agent")
    assert second.request_id != state.request_id
    loaded = store.load(state.request_id)
    assert loaded.workflow == "modify_material" and loaded.project_access == "read_only"
    assert loaded.sandbox_path == str((tmp_path / "sandbox").resolve())


def test_user_example_flow_approval_and_staged_file(store, tmp_path):
    sandbox = tmp_path / "sandbox"
    state = store.start(agent="pydyna_agent", workflow="modify_material", sandbox_path=sandbox)
    state.set_step("planning")
    staged = state.add_staged_file(sandbox / "materials_steel.k")
    action = {"action": "replace_parameter", "entity": "*MAT_024:120045", "parameter": "SIGY",
              "old_value": 340.0, "new_value": 360.0}
    approval = state.request_approval(tool="file_edit", summary="SIGY 340 -> 360", action=action)
    data = json.loads((runtime_folder(tmp_path) / f"{state.request_id}.json").read_text())
    assert data["current_step"] == "awaiting_approval"
    assert data["staged_files"] == [staged]
    assert data["pending_action"]["new_value"] == 360.0
    with pytest.raises(MemoryRefused, match="approval is still open"):
        state.set_step("applying")
    state.resolve_approval(approval, True)
    assert state.current_step == "applying" and state.pending_action["parameter"] == "SIGY"


def test_declined_approval_clears_pending_action(store):
    state = store.start(agent="pydyna_agent")
    approval = state.request_approval(tool="file_edit", summary="change", action={"action": "x"})
    state.resolve_approval(approval, False)
    assert state.pending_action is None and state.current_step == "planning"
    assert state.pending_approvals[0]["answer"] == "declined"
    with pytest.raises(MemoryRefused, match="no open approval"):
        state.resolve_approval(approval, True)
    with pytest.raises(MemoryRefused, match="no approval is open"):
        state.set_step("awaiting_approval")


def test_staged_file_outside_sandbox_refused(store, tmp_path):
    state = store.start(agent="a", sandbox_path=tmp_path / "sandbox")
    with pytest.raises(MemoryRefused, match="outside the sandbox"):
        state.add_staged_file(tmp_path / "elsewhere.k")
    with pytest.raises(MemoryRefused, match="outside the sandbox"):
        state.add_staged_file(tmp_path / "sandbox" / ".." / "escape.k")
    os.symlink(tmp_path, tmp_path / "sandbox" / "link")
    with pytest.raises(MemoryRefused, match="outside the sandbox"):
        state.add_staged_file(tmp_path / "sandbox" / "link" / "x.k")
    nobox = store.start(agent="a")
    with pytest.raises(MemoryRefused, match="no sandbox"):
        nobox.add_staged_file(tmp_path / "x.k")


def test_tool_calls_redacted_and_excerpt_cut(tmp_path):
    store = RuntimeStateStore.for_working_directory(tmp_path, result_chars=300)
    state = store.start(agent="linux_agent")
    state.record_tool_call("shell_run", {"command": "curl -H 'Authorization: Bearer abc.def.ghi' x"},
                           result="x" * 5000, audit_id=7)
    state.record_tool_call("filesystem_read", {"path": "/a"}, result="password=hunter22 in file")
    raw = (runtime_folder(tmp_path) / f"{state.request_id}.json").read_text()
    assert "Bearer abc" not in raw and "hunter22" not in raw
    first, second = state.tool_calls
    assert first["arguments"]["command"] == REDACTED and first["audit_id"] == 7
    assert first["result_chars"] == 5000 and len(first["result_excerpt"]) < 400
    assert second["result_excerpt"] == REDACTED
    assert state.current_step == "running_tool"


def test_errors_and_retry_limit(tmp_path):
    store = RuntimeStateStore.for_working_directory(tmp_path, max_retries=2)
    state = store.start(agent="a")
    assert state.record_error("timeout", retry=True) is True
    assert state.record_error("timeout again", retry=True) is False
    assert state.retries == 2 and len(state.errors) == 2
    assert store.load(state.request_id).retries == 2


def test_finish_archives_without_excerpts(store, tmp_path):
    state = store.start(agent="a")
    state.record_tool_call("filesystem_read", {"path": "/m.k"}, result="*PART\n1")
    target = store.finish(state)
    assert not (runtime_folder(tmp_path) / f"{state.request_id}.json").exists()
    data = json.loads(target.read_text())
    assert data["current_step"] == "done" and data["finished_at"]
    assert "result_excerpt" not in data["tool_calls"][0] and data["tool_calls"][0]["result_chars"] == 7
    assert store.archived() == [state.request_id]
    assert store.load(state.request_id).finished
    with pytest.raises(MemoryRefused, match="has ended"):
        state.set_step("planning")


def test_finish_without_archive_deletes_and_cancel(store, tmp_path):
    state = store.start(agent="a")
    assert store.finish(state, "failed", archive=False) is None
    assert list(runtime_folder(tmp_path).glob("REQ-*.json")) == []
    other = store.start(agent="a")
    approval = other.request_approval(tool="rm", summary="delete", action={"action": "delete"})
    store.cancel(other)
    archived = store.load(other.request_id)
    assert archived.cancelled and archived.current_step == "cancelled" and archived.pending_action is None
    assert archived.pending_approvals[0]["answer"] == "not answered" and approval
    with pytest.raises(MemoryRefused, match="ends as one of"):
        store.finish(store.start(agent="a"), "planning")


def test_unfinished_after_crash_and_archive_pruned(tmp_path):
    store = RuntimeStateStore.for_working_directory(tmp_path, keep_archived=2)
    crashed = store.start(agent="a")
    crashed.set_step("planning")
    fresh = RuntimeStateStore.for_working_directory(tmp_path)  # next start of Champion
    assert [s.request_id for s in fresh.unfinished()] == [crashed.request_id]
    assert fresh.unfinished()[0].current_step == "planning"
    for _ in range(4):
        store.finish(store.start(agent="a"))
    assert len(store.archived()) == 2


def test_bad_request_ids_refused(store):
    for bad in ("../../etc/passwd", "REQ-1", "", "REQ-20261121-0001/../x"):
        with pytest.raises(MemoryRefused, match="not a request ID"):
            store.load(bad)
    with pytest.raises(MemoryRefused, match="no runtime state"):
        store.load("REQ-20000101-0001")


def test_unknown_and_final_steps_refused(store):
    state = store.start(agent="a")
    for bad in ("done", "sleeping"):
        with pytest.raises(MemoryRefused, match="unknown step"):
            state.set_step(bad)
    state.set_step("running_tool", agent="pydyna_agent", sub_agent="keyword_lookup")
    assert store.load(state.request_id).sub_agent == "keyword_lookup"
