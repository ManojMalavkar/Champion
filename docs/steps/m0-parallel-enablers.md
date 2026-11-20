# Step M0 - Parallel enablers

M0 is the first step of the parallel plan (PLAN.md, "Parallel development"). It changes no behaviour for the user. It removes the places where track branches would edit the same files, and adds groups of agents, which PyDyna and ANSA/META need.

## Before and after

| Need of a track | Before M0 | After M0 |
| --- | --- | --- |
| Add an agent | Add an import to `champion/specialists/registry.py` | Add a package `champion/specialists/<track>/`; it is found |
| Add settings | Add a section to `DEFAULTS` in `champion/core/config.py` and to `config/config.example.yaml` | Declare the section on the spec; example in `config/examples/<section>.yaml` |
| Sub-agents | Not possible | `SpecialistSpec(members=(...))`: one group tool for the orchestrator, members inside |
| Tests in a folder | `from fake_model import` only works in `tests/` | `tests/conftest.py` makes it work in `tests/<track>/` |

## What was built

* **Discovery** (`specialists/registry.py`): `load_builtin_specialists` imports every module and package in `champion/specialists/` except `base`, `registry` and names starting with `_`. A package that fails to import is reported at chat start (`specialist module X could not be loaded (...)`) and left out; Champion still starts. Linux and Coder are found the same way; `registry.py` no longer names them.
* **Config sections of agents** (`core/config.py`): `register_config_section` (called by `register_specialist` when a spec has `config_section`). `load_config` loads the specialists first, adds each section's defaults, merges the file (unknown keys warn as before), resolves `config_path_keys` against the project folder and runs `config_validate` (errors are prefixed with the section name). A built-in section (`llm`, `edit`, ...) cannot be taken. `example_fragment_problems` checks `config/examples/`: one file per section with exactly its defaults, no file without a section.
* **Groups** (`specialists/base.py`, `agents/orchestrator.py`): a spec with `members` is a group. The orchestrator gets one tool `<group>_agent` and the line "It leads: ..." in its instructions (with the members' routing hints). The group agent gets each member as a tool `<member>_agent`, built like any specialist tool: guarded, audited, own turn limit, live tool lines. The group's instructions list its members and the rules for using them. Members are named `Group/Member` (for example `PyDyna/Keyword`) in tool lines, the audit log and error texts, and switched with `agents.<member>.enabled`. Rules: at most 2 levels, member names unique across all agents, members declare no config section.
* **Decline at every level:** `stop_after_decline` is on every agent, so a "no" inside a member ends the member, the group and the turn with the fixed answer; the model is not called again.
* **Visible groups:** the chat start line shows `pydyna_agent (keyword_agent, ...)`; `session_info` has `sub_agents`; tool lines of a member are indented one more step.

## Code flow (a group)

```text
user -> Champion (orchestrator)
          -> pydyna_agent            guarded, audit agent "Champion"
               PyDyna (group agent)
               -> keyword_agent      guarded, audit agent "PyDyna"
                    PyDyna/Keyword (member agent)
                    -> lookup_keyword  guarded, audit agent "PyDyna/Keyword"
```

## How a track adds an agent

```python
# champion/specialists/hpc/__init__.py (on branch track/hpc)
from champion.specialists.base import SpecialistSpec
from champion.specialists.registry import register_specialist

HPC = register_specialist(SpecialistSpec(
    name="hpc", title="HPC", description="...", instructions="...", tools=(...),
    config_section="hpc", config_defaults={"mjob_path": None, "timeout_seconds": 60},
    config_path_keys=("mjob_path",),
))
```

Plus `config/examples/hpc.yaml` with the same defaults, tests in `tests/hpc/test_hpc_*.py`, and the track plan in `docs/tracks/hpc.md`. No shared file changes.

## Tests

`tests/test_parallel_enablers.py` (17 tests) writes a demo track package `m0demo` (a group with sub-agents Alpha and Beta and a config section) into a temporary folder that counts as part of `champion/specialists/`. It checks discovery, a broken package, the config section (defaults, override, unknown key, validator, path key), a section that takes a built-in name, the example files, group rules, unique names, members switched off, a full run through group and member with tool lines and audit, and a "no" inside a member that ends the turn after 3 model calls. 269 tests pass on Databricks serverless (252 earlier + 17). Breaking the group wiring or the section validation in a copy makes 3 of the new tests fail.

## Verify

```bash
source ~/.ansa_meta_copilot/.venv/bin/activate
git pull && python -m pytest -q        # 269 passed
champion chat --level 1.2              # start line unchanged: coder_agent, linux_coder_agent
```

A question and an edit work as before (Linux and Coder have no members).
