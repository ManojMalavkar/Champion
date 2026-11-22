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
