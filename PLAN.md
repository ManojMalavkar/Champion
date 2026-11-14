# Champion - Plan

Champion rebuilds mChampion (`/Users/t0018k6@inetpsa.com/mChampion`) on the OpenAI Agents SDK, with the PyDyna specialist from `PyDyna_Agent_git`. It is built step by step: each step is small, runnable, tested and explained in `docs/steps/`, and the next step starts only after the user has verified the current one.

The full design (access levels, large-file handling, code review and execution, knowledge base, extensibility, policy review, risks) is in the design plan: `/Users/t0018k6@inetpsa.com/.assistant/plans-v2/plan-champion-mchampion-rebuilt-on-the-openai-agents-sd-1794659850570.md`. Sections that become relevant are copied into this file as each step is built.

## Key decisions
* Framework: OpenAI Agents SDK (`openai-agents`); specialists are used as tools (`Agent.as_tool`), never handoffs.
* Provider: direct OpenAI API key (`OPENAI_API_KEY` in the environment or `.env`). One client factory, so a gateway `base_url` can be set later.
* UI: Textual terminal UI (from step 5; earlier steps use a plain command line).
* Access levels chosen by the user: 1.1 working dir read only, 1.2 working dir read/write, 2.1 working dir + outside read only, 2.2 working dir + outside read/write, Custom. Secret files, symlinks, private keys, `/proc`, `/sys`, `/dev` always blocked.
* Material data protection: not used.
* Large file: 100 MiB or more (`pydyna.large_file_bytes`); hard limit `pydyna.max_model_bytes` (8 GiB, to be confirmed).
* Generated code runs on the host without a sandbox, after a reviewer agent (policy and intent), a static check and user approval; a runtime guard watches file and process use.
* Specialist plug-ins (`champion/specialists/`) so ANSA/META and PRIMER agents can be added later.
* mChampion and PyDyna_Agent_git are read only; nothing in them is changed.

## Rules for every step
* Ends with a command the user can run.
* Roughly 200-600 lines of new code.
* Has a walkthrough in `docs/steps/NN-name.md` (what was built, concepts, code flow, how to verify).
* Adds tests; all earlier tests must still pass.
* Stops for the user's OK before the next step.

## Steps

### Step 0 - Skeleton, config and `champion doctor`
- [x] `pyproject.toml`, `README.md`, `.gitignore`, `config/config.example.yaml`
- [x] `champion/core/config.py`: defaults, YAML merge, validation, path resolution, OpenAI key lookup (never printed)
- [x] `champion/doctor.py` and `champion/cli.py`: `champion doctor` and `champion --version`
- [x] Tests: `tests/test_config.py`, `tests/test_doctor.py`
- [x] Walkthrough: `docs/steps/00-scaffold.md`
- [ ] User verified

Verify: `pip install -e '.[dev]'`, `pytest`, `champion doctor` (and `champion doctor --online` with a real key).

Status: built and pushed (commit "Step 0"); 40 tests pass and `champion doctor` gives the expected results on Databricks serverless (Python 3.12, openai-agents 0.24.0). Waiting for the user's check on the Linux machine.

### Step 1 - First agent in a plain command-line chat
One Agent, `Runner.run`, one harmless tool, printed tool calls. Learn: Agent, Runner, `function_tool`, how the SDK replaces the hand-built loop from lab 3.
- [x] `champion/core/context.py` (`ChampionContext`), `champion/core/hooks.py` (`ToolEventRecorder`)
- [x] `champion/core/runtime.py`: one `AsyncOpenAI` client, `OpenAIResponsesModel`, tracing off, in-memory `SQLiteSession` trimmed to `orchestration.history_turns` (new key, default 10), `max_turns` = `orchestration.max_steps`
- [x] `champion/agents/assistant.py` with the `session_info` tool (`champion/tools/session_info.py`, reads no files)
- [x] `champion/chat.py` and `champion chat [-q]` (`/help`, `/new`, `/usage`, `/exit`)
- [x] Tests with a scripted `FakeModel` (no network): `tests/test_runtime.py`, `tests/test_chat.py`
- [x] Walkthrough: `docs/steps/01-first-agent.md`
- [ ] User verified

Verify: `pytest` (56), `champion chat -q "What time is it and which folder am I in?"`, then `champion chat`.

Status: built; 56 tests pass on Databricks serverless; scripted chat output checked; real model wiring reached the OpenAI API (invalid test key gave HTTP 401, shown in one line). A real answer needs the user's key. `openai-agents` pinned to `>=0.24,<1`.

### Step 2 - Access levels and file tools
Levels 1.1, 1.2, 2.1, 2.2, Custom; `filesystem_read`, `filesystem_list` with level checks; `--level`. Learn: run context, `is_enabled`, why checks live inside tools.
- [ ] Not started

### Step 3 - PyDyna agent on its own (`champion --pydyna`)
The six knowledge tools ported from PyDyna_Agent_git on the existing vector DB. Learn: porting tools, prompts, untrusted search results.
- [ ] Not started

### Step 4 - Orchestrator with specialists as tools
PyDyna and Linux coder as tools; shell rules; specialist registry. Learn: `Agent.as_tool`, nested runs, `tool_choice`.
- [ ] Not started

### Step 5 - Textual TUI
Streaming output, level selector and `/level`, status bar, audit log. Learn: `Runner.run_streamed` events.
- [ ] Not started

### Step 6 - Large-file reading
Keyword index, `model_outline`, `model_find`, `model_read_range`; small/large split at 100 MiB. Learn: streaming parsing, cached index.
- [ ] Not started

### Step 7 - Editing
`pydyna_edit_model` patch format, approval, diff, undo. Learn: atomic replace, approval pause/resume.
- [ ] Not started

### Step 8 - Code execution
Static check, runtime guard, `HostBackend`, approval. Learn: AST checks, audit hooks and their limits.
- [ ] Not started

### Step 9 - Reviewer agent
Policy and intent review controlling `pydyna_execute`. Learn: typed output, agent clones, review tied to the code hash.
- [ ] Not started

### Step 10 - Knowledge base command
`champion kb extract/build/status`, including `pydpf_post`. Learn: Chroma build, staged collection swap.
- [ ] Not started

### Step 11 - Hardening and final check
README, full test run, confirm mChampion and PyDyna_Agent_git unchanged.
- [ ] Not started

## Open decisions
* `pydyna.max_model_bytes` value (8 GiB for now).
* PRIMER = Oasys PRIMER with JavaScript (to confirm).
* Approval for writes inside scope at levels 1.2/2.2 (currently: only deletes and model edits ask).
* Where Champion runs for verification (Linux workstation) and whether Champion becomes a Git repository.
