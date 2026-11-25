# CR-1 - Core tests do not see track agents

Core request from track T1 (PyDyna), 21 Nov 2026, built on `main` (PLAN.md P.6). Test setup only: Champion itself does not change.

## Problem

M0 finds agents in `champion/specialists/<track>/` without editing shared files. But 11 core tests expect only the Coder and Linux agents to be offered. When `track/pydyna` registers its group `pydyna` (on by default; `agents.pydyna.enabled: True` is in `config.py` `DEFAULTS`), they fail, and a track may not edit core tests (P.2). Every later track that registers an agent would hit the same problem.

Failing on `track/pydyna` without this change: `test_filesystem_tools` (1), `test_linux_specialist` (2), `test_parallel_enablers` (4), `test_runtime` (1), `test_specialists` (3).

## Change

* `tests/conftest.py`: `CORE_AGENTS` (`coder`, `linux_coder`), `hide_track_agents(monkeypatch)` and an autouse fixture. For a test file directly in `tests/`, the fixture loads the built-in specialists and leaves every agent found then, other than the core agents, out of `registry.registered_specialists()`. Agents a test registers itself afterwards (the dummy specs) stay visible. Tests in `tests/<track>/` see every agent.
* `tests/test_core_tests_hide_track_agents.py` (2 tests): a core test sees only the core agents; a stand-in track agent is hidden, an agent registered by the test stays, and `select_specialists` offers Coder, Linux and the test agent.
* A new core agent (a module directly in `champion/specialists/`) must be added to `CORE_AGENTS`.

Not chosen: switching track agents off by default or renaming them (changes the tool names), or editing each core test.

## Checks (Databricks serverless, copies, 21 Nov 2026)

| Copy | Result |
| --- | --- |
| `main` before | 269 passed |
| `main` with CR-1 | 271 passed (269 + 2 new) |
| `track/pydyna` (T1.0 and tools, uncommitted) with CR-1 | 310 passed, 5 skipped |
| same, fixture switched off | 13 failed (the 11 core tests and the 2 new ones) |

The 5 skipped tests need the PyDyna data on the Linux machine.

## Verify (Linux, after pull)

```bash
source ~/.ansa_meta_copilot/.venv/bin/activate
cd <main worktree>
git pull
python -m pytest -q    # 271 passed
```

## Follow-up after M1 (T1 PyDyna, 24 Nov 2026)

### Problem

Since M1 a track also registers its own config section (`SpecialistSpec.config_section`) and its knowledge base (`register_knowledge`). CR-1 hid only the agents, so with T1.1 on `track/pydyna` (section `pydyna`, four PyDyna collections) 3 core tests failed:

* `test_config.py::test_defaults_used_when_no_file`: `config.get("pydyna")` is the PyDyna section, not `None`.
* `test_doctor.py::test_knowledge_line_without_declared_bases`: no "no agent declares a knowledge base" line.
* `test_parallel_enablers.py::test_every_agent_section_has_its_example_file`: `pydyna: example file pydyna.yaml is missing` (the test points at an empty folder).

### Change (test setup only)

* `tests/conftest.py`: `hide_track_agents` also hides the config sections owned by the hidden agents (`config_sections`), and the knowledge bases declared before the test starts other than by a core agent (`knowledge_specs`; `knowledge_spec` raises the usual "no knowledge base is declared" error). Some modules import these functions by name (`knowledge/status.py`, `search.py`, `builder.py`), so the filtered function replaces every reference to the original in the loaded `champion` modules. As before, sections and knowledge bases a test registers itself stay visible.
* Marker `@pytest.mark.track_agents`: the fixture leaves such a core test alone. Used for `test_the_repository_examples_match_the_agents`, which checks `config/examples/` against every agent of the repository, track packages included.
* `tests/test_core_tests_hide_track_agents.py` (+2): a stand-in track with section and knowledge base is hidden from `config_sections`, `load_config`, `example_fragment_problems`, `knowledge_specs` (also through `status.py`), `knowledge_spec` and `knowledge_status`; a section and knowledge base registered by the test stay visible; a marked test sees the original functions.

Not chosen: deleting the entries from the registries for the test (`monkeypatch.delitem`) - a test that registers and unregisters its own track would get it back at teardown and leak it into later tests; editing the three core tests (they may not know track names).

### Checks (Databricks serverless, copies, 24 Nov 2026)

| Copy | Result |
| --- | --- |
| `main` (`6e369af`, M1) with the follow-up | 379 passed (377 + 2 new) |
| `track/pydyna` with M1 and T1.1 (uncommitted), without the follow-up | 521 passed, 3 failed, 5 skipped |
| same, with the follow-up | 526 passed, 5 skipped |

The 5 skipped tests need the PyDyna data on the Linux machine. Files: `tests/conftest.py`, `tests/test_core_tests_hide_track_agents.py`, `tests/test_parallel_enablers.py` (one marker), this walkthrough.

### Verify (Linux, after pull)

```bash
cd <main worktree>
git pull
python -m pytest -q    # 379 passed with M1 (more once other main work is in)
```
