# Champion - Plan

Champion rebuilds mChampion (`/Users/t0018k6@inetpsa.com/mChampion`) on the OpenAI Agents SDK, with the PyDyna specialist from `PyDyna_Agent_git`. It is built step by step: each step is small, runnable, tested and explained in `docs/steps/`, and the next step starts only after the user has verified the current one.

The full design (access levels, large-file handling, code review and execution, knowledge base, extensibility, policy review, risks) is in the design plan: `/Users/t0018k6@inetpsa.com/.assistant/plans-v2/plan-champion-mchampion-rebuilt-on-the-openai-agents-sd-1794659850570.md`. Sections that become relevant are copied into this file as each step is built.

## Key decisions
* Framework: OpenAI Agents SDK (`openai-agents`); specialists are used as tools (`Agent.as_tool`), never handoffs.
* Provider: direct OpenAI API key (`OPENAI_API_KEY` in the environment or `.env`). One client factory, so a gateway `base_url` can be set later.
* UI: Textual terminal UI (from step 9; earlier steps use a plain command line).
* Access levels chosen by the user: 1.1 working dir read only, 1.2 working dir read/write, 2.1 working dir + outside read only, 2.2 working dir + outside read/write, Custom. Secret files, symlinks, private keys, `/proc`, `/sys`, `/dev` always blocked.
* Material data protection: not used.
* Large file: 100 MiB or more (`pydyna.large_file_bytes`); hard limit `pydyna.max_model_bytes` (8 GiB, to be confirmed).
* Generated code runs on the host without a sandbox, after a reviewer agent (policy and intent), a static check and user approval; a runtime guard watches file and process use.
* Specialist plug-ins (`champion/specialists/`); agents built on separate branches in the order Linux, PyDyna, ANSA/META, PRIMER, on top of shared foundations (policy, plug-ins, skills, MCP, LS-DYNA library, execution, knowledge base, TUI) built first on `main`.
* Champion is the Git folder `Champion-git` (remote `ManojMalavkar/Champion`); one commit per step; agent branches merged by pull request.
* mChampion, PyDyna_Agent_git and ANSA-META_CodeRAG are read only; nothing in them is changed.

## Rules for every step
* Ends with a command the user can run.
* Roughly 200-600 lines of new code.
* Has a walkthrough in `docs/steps/NN-name.md` (what was built, concepts, code flow, how to verify).
* Adds tests; all earlier tests must still pass.
* Updates `docs/REPORT.md` (presentation report): progress log, metrics, safety status, demo script and likely questions.
* Is committed and pushed as one commit.
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

## Roadmap after step 1

Three phases. Phase A builds everything shared on `main`, so no agent branch has to build it again. Phase B builds one branch per agent, in the order 1 Linux, 2 PyDyna, 3 ANSA/META, 4 PRIMER. Phase C integrates and hardens.

What is shared and what is per agent:

| Part | Shared framework (phase A, on `main`) | Per-agent content (phase B, on the agent branch) |
| --- | --- | --- |
| Policy | Access levels, scope checks, blocked paths, tool-policy registry, audit log | Policy entries for the agent's own tools |
| Tools | Tool wrapper (policy check, output cap, audit), generic file tools | The agent's own tools |
| Specialists | `SpecialistSpec`, registry, orchestrator built from the registry | The agent's spec, prompt, routing hints |
| Skills | Skill format, registry, `list_skills` / `load_skill` tools, ready code snippets | The agent's skills (workflows, checklists, tested code templates) |
| MCP | MCP client (stdio), per-server tool allowlist, policy mapping, approval | MCP servers the agent uses, if any |
| LS-DYNA files | Keyword index, outline/find/read range, patch-edit engine with diff and undo | Used by PyDyna and PRIMER |
| Code execution | Backend interface, static-check interface, approval, review records by code hash, reviewer factory, Python guard | Concrete backend: host Python (PyDyna), ANSA/META batch, PRIMER batch (JavaScript) |
| Knowledge base | `champion kb extract/build/status`, projects registered by specialists | The agent's collections (lsdyna_keywords, pydyna_api, pydpf_post, ansa_api, meta_api, primer_js) |
| UI | Textual TUI reading agents, approvals and events from the registry | Nothing (appears automatically) |

### Phase A - shared foundations on `main` (sequential)

#### Step 2 - Access levels, policy and generic file tools
Levels 1.1, 1.2, 2.1, 2.2, Custom; `scope.py`, hardened `sensitive.py`, `tool_policy.py`; tool wrapper (policy check, output cap, audit record); audit log (SQLite); `filesystem_read`, `filesystem_list`, `filesystem_disk_usage`; `champion chat --level`. Learn: run context, `is_enabled`, why checks live inside tools.
- [x] `champion/policy/levels.py`, `sensitive.py` (system folders, secret names, OS folders never written), `scope.py` (`check_path`, no symbolic links, `open_no_symlinks`), `tool_policy.py` (deny by default)
- [x] `champion/storage/audit.py` (SQLite, mode 600, results not stored)
- [x] `champion/tools/guard.py` (`run_guarded`, `requires` for `is_enabled`, output cap) and `champion/tools/filesystem.py` (3 read-only tools; `.key` read only if LS-DYNA keyword file; private keys, binary and files of 100 MiB or more refused)
- [x] Level and audit in `ChampionContext`; `champion chat --level`; chat `/level`, `/level NAME`, `/audit`
- [x] Tests: `tests/test_policy.py`, `tests/test_filesystem_tools.py`
- [x] Walkthrough: `docs/steps/02-access-levels.md`
- [ ] User verified

Verify: `pytest` (103), `champion chat --level 1.1`, then the checks in the walkthrough.

Status: built; 103 tests pass on Databricks serverless; scripted chat checked (blocked read at 1.1, `/level 2.1` allows it, `/proc` blocked at every level, `/audit` rows). Write tools come with the agents that need them; the scope check for writing is already in place and tested.

#### Step 3 - Specialist plug-in interface and orchestrator skeleton
`champion/specialists/base.py` (`SpecialistSpec`) and `registry.py`; orchestrator built from enabled specialists with `Agent.as_tool`; routing hints and `tool_choice`; contract tests with a dummy specialist (offered to the orchestrator, rejected if a tool has no policy entry). Learn: agents as tools, nested runs.
- [ ] Not started

#### Step 4 - Skills framework
Skill format (folder with `skill.yaml`, `instructions.md`, optional `snippets/` of tested code and optional `helpers.py`), registry, `list_skills` and `load_skill` tools offered only to compatible specialists, skills loaded on demand (not all in the prompt). Port mChampion's `cae.crash_loadcase` as the first skill. Learn: progressive disclosure, keeping prompts small.
- [ ] Not started

#### Step 5 - MCP framework
MCP client using the SDK's MCP server support (stdio first), `mcp.servers` config, per-server tool allowlist, every MCP tool mapped to a policy entry and approval rule, servers attached to specific specialists through their spec, clean start and stop. Test with a small local MCP server. Learn: MCP, treating external tools as untrusted.
- [ ] Not started

#### Step 6 - Shared LS-DYNA file library
Keyword index (streamed, cached for files of 100 MiB or more), `model_outline`, `model_find`, `model_read_range`, patch-edit engine (exact old text, streamed atomic apply, diff, reverse-patch undo, approval). Learn: streaming parsing, atomic replace, approval pause and resume.
- [ ] Not started

#### Step 7 - Code execution framework
`ExecutionBackend` interface, static-check interface (Python AST first), runtime guard for Python, approval flow, review records keyed by code hash, generic reviewer factory with typed verdict (policy and intent), `HostPythonBackend`. Learn: layered defence, audit hooks and their limits.
- [ ] Not started

#### Step 8 - Knowledge base command
`champion kb extract/build/status`, staging-collection swap, cosine distance, manifest; projects registered by specialists. Learn: Chroma build, safe replace.
- [ ] Not started

#### Step 9 - Textual TUI
Streaming output, level selector and `/level`, status bar, tool events, approval screens (edits, code runs, MCP calls), audit view; agents and approvals read from the registry. Learn: `Runner.run_streamed` events.
- [ ] Not started

### Phase B - one branch per agent

Each branch is created from `main` after the steps it needs are merged, has its own sub-steps (one commit each, same rules as above), adds files only under its own package, config section and `docs/steps/`, and is merged through a GitHub pull request. PLAN.md and REPORT.md are updated on `main` at merge time.

| Order | Branch | Needs from phase A | Content |
| --- | --- | --- | --- |
| 1 | `agent/linux` | Steps 2-5 | Linux specialist: process list, shell tool with command rules and approval, disk analysis; skills `linux.disk_analysis`, `hpc.job_submission` (later) |
| 2 | `agent/pydyna` | Steps 2-8 | PyDyna specialist: 6 knowledge tools + `search_pydpf_post`, model tools on the shared LS-DYNA library, `pydyna_count_nodes`, `pydyna_local_materials`, `pydyna_edit_model`, reviewer and `pydyna_execute` on the host backend; kb projects `pydyna`, `pydpf_post`; skills `cae.model_check`, PyDyna code templates |
| 3 | `agent/ansa-meta` | Steps 2-8 | ANSA/META Python API specialist based on ANSA-META_CodeRAG: knowledge tools on `ansa_meta.knowledge_root`, ANSA and META batch backends (licensed batch runs), static check for ANSA/META scripts; kb projects `ansa_api`, `meta_api`; skills with ANSA/META script templates |
| 4 | `agent/primer` | Steps 2-8 | Oasys PRIMER automation specialist: JavaScript API knowledge on `primer.knowledge_root`, PRIMER batch backend (licensed batch runs, JavaScript), JavaScript static check, reuse of the LS-DYNA library; kb project `primer_js`; skills with PRIMER script templates |

The Linux branch can start right after step 5; its review can overlap with steps 6-8 on `main`. At most two branches are open at the same time.

### Phase C - integration and hardening

#### Step 10 - Integration
All four specialists enabled together; cross-agent routing tests (one question per agent, mixed questions); cross-agent skills (for example model check across PyDyna and PRIMER); demo script updated.
- [ ] Not started

#### Step 11 - Hardening and final check
README, full test run, security review of every tool and MCP entry, confirm mChampion, PyDyna_Agent_git and ANSA-META_CodeRAG unchanged.
- [ ] Not started

## Skills: when and where
* The skills framework is built once in phase A (step 4), before any agent branch, so agents never build it again.
* Skill content is written on each agent branch together with the agent, because a skill must match that agent's tools. Cross-agent skills are added in step 10.
* Ready code ("code skills"): tested snippets and helper functions in a skill's `snippets/` or `helpers.py`, for example reading parts from a deck, renumbering IDs, ANSA/META batch script skeletons, PRIMER script skeletons. Agents load and adapt them instead of writing code from scratch, and the reviewer checks them like any other generated code.
* Skills only contain instructions and code; they never run anything by themselves. Running still goes through the tool, review and approval path.

## Knowledge base layout (same for every agent)
Each knowledge-based agent (PyDyna, ANSA/META, PRIMER) has one knowledge root with the same two folders:

```
<knowledge_root>/
  vector_db/        Chroma database (chroma.sqlite3 + collection folders)
  knowledge-base/   JSONL source files (keyword_reference.jsonl, *_docs.jsonl, *_source*.jsonl)
```

* Config: one key per agent, `<agent>.knowledge_root` (for example `pydyna.knowledge_root`, `ansa_meta.knowledge_root`, `primer.knowledge_root`); `vector_db/` and `knowledge-base/` are derived from it. Explicit paths (such as `pydyna.database_path`) stay possible as overrides.
* The shared knowledge layer (step 8) opens any root with this layout, checks it (`champion kb status`, `champion doctor`) and builds into it (`champion kb build`); agents only declare their collection names and JSONL files.
* Step 8 replaces `pydyna.database_path` / `pydyna.keyword_reference_path` defaults with `pydyna.knowledge_root`.

## MCP plan
* Step 5 builds the MCP client framework. Each agent's MCP servers are added later on its branch or after phase C.
* Planned servers per agent: a knowledge-retrieval MCP server (the agent's knowledge base search exposed over MCP), web search, mail handling.
* Knowledge retrieval over MCP: knowledge tools are written as plain functions (as in step 1), so the same code can later be served by a Champion MCP server (`champion mcp serve-kb --agent pydyna`) without rewriting.
* Rules the framework enforces from step 5:
  * every MCP tool needs a policy entry: read-only, sends data out of the machine (web search), or acts on someone's behalf (sending mail);
  * sending mail or any outbound action always asks for approval and shows the full content first;
  * web search and mail content are untrusted input: shown to the model as data, never as instructions;
  * reading mail is off unless enabled per agent in config;
  * servers that reach the internet are marked in `champion doctor` and the TUI status bar.

## Resolved decisions
* ANSA, META and PRIMER are installed on the Linux workstation with licences for batch runs, so the ANSA/META and PRIMER branches get real batch backends (config: executable paths per agent). (User, in conversation.)
* PRIMER is Oasys PRIMER, scripted in JavaScript. (User, in conversation.)
* Knowledge base layout: `vector_db/` (Chroma) and `knowledge-base/` (JSONL) per agent, the same for PyDyna, ANSA/META and PRIMER. (User, in conversation.)
* MCP: knowledge-retrieval MCP per agent later, plus web search and mail handling. (User, in conversation.)

## Open decisions
* `pydyna.max_model_bytes` value (8 GiB for now).
* Approval for writes inside scope at levels 1.2/2.2 (currently: only deletes and model edits ask).
* Which web search and mail MCP servers (provider, company approval for sending data out).
