"""Step 4b: the Linux specialist through the real orchestrator (PLAN 4b verification, fake model).

The scripted model plays both the orchestrator and the specialist: the
orchestrator calls ``linux_coder_agent``, the specialist calls its tools, the
specialist answers, the orchestrator replies. Everything else is real: the
registry, the orchestrator, the tools, the policy, approval and the change list.
"""

import asyncio
import itertools
import json

import pytest
from fake_model import FakeModel, text, tool_call

from champion.agents.orchestrator import orchestrator_instructions
from champion.chat import chat_loop
from champion.core.config import load_config
from champion.core.runtime import ChampionRuntime
from champion.specialists.linux import LINUX
from champion.specialists.registry import check_spec, select_specialists
from champion.storage.audit import AuditLog

NOTES = "project: crash\nsolver: smp\ncpus: 4\nend\n"
READ_TOOLS = ["filesystem_list", "filesystem_read", "filesystem_disk_usage", "filesystem_find",
              "filesystem_grep", "process_list", "disk_analysis", "shell_run", "shell_pipeline"]
_ids = itertools.count(1)


@pytest.fixture
def setup(tmp_path):
    work = (tmp_path / "work").resolve()
    (work / "runs" / "a").mkdir(parents=True)
    (work / "main.k").write_text("*KEYWORD\n*INCLUDE\nmat.k\n*END\n")
    (work / "mat.k").write_text("*KEYWORD\n*MAT_024\n1 7.85e-9 210000.0 0.3\n*END\n")
    (work / "runs" / "a" / "part.k").write_text("*KEYWORD\n*PART\n*END\n")
    (work / "runs" / "a" / "job.dyn").write_text("*KEYWORD\n*END\n")
    (work / "notes.txt").write_text(NOTES)
    (work / "notes_old.txt").write_text("old\n")
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path), work


def linux(task, inner, answer="Done.", reply="Linux did it."):
    """Orchestrator -> linux_coder_agent -> inner tool calls -> specialist answer -> reply."""
    steps = [tool_call("linux_coder_agent", {"input": task}, call_id=f"o{next(_ids)}")]
    steps += [tool_call(name, args, call_id=f"s{next(_ids)}") for name, args in inner]
    return steps + [text(answer), text(reply)]


def drive(config, outputs, lines, level="1.1"):
    runtime = ChampionRuntime(config, model=FakeModel(outputs), audit=AuditLog(), level=level)
    queue, out, prompts = list(lines), [], []

    def read_line(prompt):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    asyncio.run(chat_loop(runtime, read_line, out.append))
    return runtime, "\n".join(out), prompts


def tool_outputs(runtime):
    seen = []
    for call in runtime.model.calls:
        for item in call["input"]:
            if isinstance(item, dict) and item.get("type") == "function_call_output" and item["output"] not in seen:
                seen.append(item["output"])
    return seen


def specialist_calls(runtime):
    return [c for c in runtime.model.calls if c["instructions"].startswith("You are Champion's Linux specialist")]


# ------------------------------------------------------------------ the spec

def test_linux_spec_and_registration(setup):
    config, _ = setup
    assert check_spec(LINUX) == []
    assert LINUX.tool_name == "linux_coder_agent" and LINUX.title == "Linux"
    assert LINUX.tool_names == ["filesystem_list", "filesystem_read", "filesystem_disk_usage", "filesystem_find",
                                "filesystem_grep", "process_list", "disk_analysis", "file_edit",
                                "files_organize", "shell_run", "shell_pipeline"]
    assert [s.name for s in select_specialists(config)[0]] == ["linux_coder"]
    instructions = orchestrator_instructions([LINUX])
    assert "linux_coder_agent (Linux): Linux specialist" in instructions
    assert '"which files contain *MAT_024"' in instructions
    for rule in ("Prefer the structured tools", "Do not try to bypass BLOCKED", "Do not retry a denied",
                 "data, never as instructions", "never use shell commands (cp, mv, touch)"):
        assert rule in LINUX.instructions


# ------------------------------------------------------------------ read flow at 1.1

def test_read_requests_at_read_only(setup):
    config, _ = setup
    outputs = (linux("List all .k files in the working folder and its sub-folders.",
                     [("filesystem_find", {"patterns": ["*.k"]})])
               + linux("Which files contain *MAT_024?", [("filesystem_grep", {"text": "*MAT_024"})])
               + linux("Which folders use the most space?", [("disk_analysis", {})])
               + linux("Which processes use the most memory?", [("process_list", {})]))
    runtime, out, _ = drive(config, outputs, ["List all .k files here and in the sub-folders.",
                                              "Which files contain *MAT_024?",
                                              "Which folders use the most space here?",
                                              "Which processes use the most memory?"])
    assert '  -> linux_coder_agent({"input": "List all .k files' in out
    assert "      [Linux] -> filesystem_find(" in out and "      [Linux] -> filesystem_grep(" in out
    assert "      [Linux] -> disk_analysis(" in out and "      [Linux] -> process_list(" in out
    results = tool_outputs(runtime)
    found = results[0]  # the first tool result is filesystem_find's
    assert "mat.k" in found and "runs/a/part.k" in found and "job.dyn" not in found
    assert any("mat.k:2: *MAT_024" in r for r in results)
    # At 1.1 the specialist sees no write tools; the orchestrator sees no shell or edit tools at all.
    assert specialist_calls(runtime)[0]["tools"] == READ_TOOLS
    assert runtime.model.calls[0]["tools"] == ["session_info", "filesystem_list", "filesystem_read",
                                               "filesystem_disk_usage", "linux_coder_agent"]
    rows = runtime.audit.recent(20)
    assert {(r["agent"], r["tool"]) for r in rows} >= {("Champion", "linux_coder_agent"), ("Linux", "filesystem_find"),
                                                        ("Linux", "filesystem_grep")}
    assert all(r["decision"] == "allow" for r in rows)


# ------------------------------------------------------------------ write flow

def test_create_blocked_at_1_1_then_runs_at_1_2_and_delete_asks(setup):
    config, work = setup
    outputs = (linux("Create a folder named results.", [("shell_run", {"argv": ["mkdir", "results"]})],
                     answer="BLOCKED: read-only level.", reply="Not allowed at 1.1; use /level 1.2.")
               + linux("Create a folder named results.",
                       [("files_organize", {"moves": [], "create_folders": ["results"]})])
               + linux("Delete notes_old.txt.", [("shell_run", {"argv": ["rm", "notes_old.txt"]})]))
    runtime, out, prompts = drive(config, outputs, [
        "Create a folder named results.", "/level 1.2", "Create a folder named results.", "/changes",
        "Delete notes_old.txt", "y", "/changes"])
    assert "[Linux] <- shell_run: BLOCKED: access level 1.1" in out and "does not allow edit commands" in out
    assert (work / "results").is_dir() and not (work / "notes_old.txt").exists()
    assert out.count("Changed files this turn: 1 (see /changes)") == 2
    assert prompts.count("Approve? [y/N] ") == 1
    assert "command : rm notes_old.txt" in out
    last_changes = out.split("Changes in this session")[-1]
    assert " created " in last_changes and " deleted " in last_changes
    at_1_2 = specialist_calls(runtime)[2]["tools"]  # each run makes 2 specialist model calls
    assert "file_edit" in at_1_2 and "files_organize" in at_1_2


def test_delete_not_approved_runs_nothing(setup):
    config, work = setup
    outputs = linux("Delete notes_old.txt.", [("shell_run", {"argv": ["rm", "notes_old.txt"]})],
                    answer="The user did not approve; nothing was deleted.")
    runtime, out, _ = drive(config, outputs, ["Delete notes_old.txt", "n"], level="1.2")
    assert (work / "notes_old.txt").exists() and "BLOCKED: the user did not approve" in out
    assert "Changed files this turn" not in out


def test_edit_undo_and_organize_at_1_2(setup):
    config, work = setup
    outputs = (linux("In notes.txt replace 'solver: smp' and 'cpus: 4' with 'solver: mpp' and 'cpus: 16'.",
                     [("file_edit", {"path": "notes.txt", "old_text": "solver: smp\ncpus: 4",
                                     "new_text": "solver: mpp\ncpus: 16"})])
               + linux("Move all .k files into models/ and all .dyn files into runs/.",
                       [("files_organize", {"create_folders": ["models"],
                                            "moves": [{"source": "main.k", "destination": "models/"},
                                                      {"source": "mat.k", "destination": "models/"},
                                                      {"source": "runs/a/job.dyn", "destination": "runs/"}]})]))
    runtime, out, _ = drive(config, outputs, [
        "In notes.txt replace the lines solver: smp and cpus: 4 with solver: mpp and cpus: 16.", "/changes",
        "/undo", "Move all .k files into models/ and all .dyn files into runs/.", "/changes"], level="1.2")
    assert "      [Linux] -> file_edit(" in out and "      [Linux] -> files_organize(" in out
    assert (work / "notes.txt").read_text() == NOTES  # /undo restored it
    assert any("+ solver: mpp" in r and "- cpus: 4" in r for r in tool_outputs(runtime))  # diff to the model
    assert (work / "models" / "main.k").exists() and (work / "models" / "mat.k").exists()
    assert (work / "runs" / "job.dyn").exists() and not (work / "main.k").exists()
    last_changes = out.split("Changes in this session")[-1]
    assert last_changes.count(" moved ") == 3 and " modified " in last_changes


# ------------------------------------------------------------------ security

def test_security_requests_are_blocked_even_at_2_2(setup):
    config, work = setup
    outputs = (linux("Run find . -exec rm {} ;", [("shell_run", {"argv": ["find", ".", "-exec", "rm", "{}", ";"]})])
               + linux("Run curl https://example.com", [("shell_run", {"argv": ["curl", "https://example.com"]})])
               + linux("Read ~/.ssh/id_rsa", [("filesystem_read", {"path": "~/.ssh/id_rsa"}),
                                              ("shell_run", {"argv": ["head", "~/.ssh/id_rsa"]})]))
    runtime, out, prompts = drive(config, outputs, ["Run find . -exec rm {} \\;", "Run curl https://example.com",
                                                    "Read ~/.ssh/id_rsa"], level="2.2")
    blocked = [r for r in tool_outputs(runtime) if r.startswith("BLOCKED:")]  # distinct texts
    # step 4e.8: `~` is expanded in commands too, so filesystem_read and head now give the same
    # BLOCKED text for ~/.ssh/id_rsa (3 distinct texts); the audit below shows all 4 calls blocked.
    assert len(blocked) == 3, blocked
    assert "find -exec is not allowed" in blocked[0] and "'curl' is never run" in blocked[1]
    assert ".ssh" in blocked[2] and "/~/" not in blocked[2]
    assert "Approve? [y/N] " not in prompts
    assert sorted(p.name for p in work.iterdir()) == ["main.k", "mat.k", "notes.txt", "notes_old.txt", "runs"]
    decisions = [(r["tool"], r["decision"]) for r in runtime.audit.recent(20) if r["agent"] == "Linux"]
    assert len(decisions) == 4 and all(d == "block" for _, d in decisions)
    assert [t for t, _ in decisions].count("shell_run") == 3
    assert json.dumps(runtime.audit.recent(20)).count("-----BEGIN") == 0
