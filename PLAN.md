# Champion - Plan

Champion rebuilds mChampion (`/Users/t0018k6@inetpsa.com/mChampion`) on the OpenAI Agents SDK, with the PyDyna specialist from `PyDyna_Agent_git`. It is built step by step: each step is small, runnable, tested and explained in `docs/steps/`, and the next step starts only after the user has verified the current one.

The full design (access levels, large-file handling, code review and execution, knowledge base, extensibility, policy review, risks) is in the design plan: `/Users/t0018k6@inetpsa.com/.assistant/plans-v2/plan-champion-mchampion-rebuilt-on-the-openai-agents-sd-1794659850570.md`. Sections that become relevant are copied into this file as each step is built.

## Key decisions
* Framework: OpenAI Agents SDK (`openai-agents`) only; specialists are used as tools (`Agent.as_tool`), never handoffs. No adapter layer for other frameworks (LangGraph, CrewAI): the safety core (config, levels, blocked paths, tool rules, audit) and the tool logic are plain Python, so only the agent wiring and the runtime would change if needed.
* Provider: direct OpenAI API key, read from the environment variable named in `llm.api_key_env` (default `OPENAI_API_KEY`) or `.env`. One client factory, so a gateway `base_url` can be set later.
* UI: Textual terminal UI (fast demo step 6; earlier steps use a plain command line).
* Build order: fast demo order (below) first, as soon as possible for the interview; everything on `main` until the demo; the rest of the roadmap follows after the demo.
* Access levels chosen by the user: 1.1 working dir read only, 1.2 working dir read/write, 2.1 working dir + outside read only, 2.2 working dir + outside read/write, Custom. Secret files, symlinks, private keys, `/proc`, `/sys`, `/dev` always blocked.
* Material data protection: not used.
* Large file: 100 MiB or more (`pydyna.large_file_bytes`); hard limit `pydyna.max_model_bytes` (8 GiB, to be confirmed).
* Generated code runs on the host without a sandbox, after a reviewer agent (policy and intent), a static check and user approval; a runtime guard watches file and process use.
* Specialist plug-ins (`champion/specialists/`). For the demo, the Linux and PyDyna (knowledge) specialists are built on `main`; after the demo, further agent work (PyDyna model tools, ANSA/META, PRIMER) is built on separate branches on top of the remaining shared foundations.
* Champion is the Git folder `Champion-git` (remote `ManojMalavkar/Champion`); one commit per step; agent branches merged by pull request.
* mChampion, PyDyna_Agent_git and ANSA-META_CodeRAG are read only; nothing in them is changed.

## Rules for every step
* Ends with a command the user can run.
* Roughly 200-900 lines of new code; two closely related pieces may share one step (fast demo order).
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

### Step 2 - Access levels, policy and generic file tools
Levels 1.1, 1.2, 2.1, 2.2, Custom; `scope.py`, hardened `sensitive.py`, `tool_policy.py`; tool wrapper (policy check, output cap, audit record); audit log (SQLite); `filesystem_read`, `filesystem_list`, `filesystem_disk_usage`; `champion chat --level`. Learn: run context, `is_enabled`, why checks live inside tools.
- [x] `champion/policy/levels.py`, `sensitive.py` (system folders, secret names, OS folders never written), `scope.py` (`check_path`, no symbolic links, `open_no_symlinks`), `tool_policy.py` (deny by default)
- [x] `champion/storage/audit.py` (SQLite, mode 600, results not stored)
- [x] `champion/tools/guard.py` (`run_guarded`, `requires` for `is_enabled`, output cap) and `champion/tools/filesystem.py` (3 read-only tools; `.key` read only if LS-DYNA keyword file; private keys, binary and files of 100 MiB or more refused)
- [x] Level and audit in `ChampionContext`; `champion chat --level`; chat `/level`, `/level NAME`, `/audit`
- [x] Tests: `tests/test_policy.py`, `tests/test_filesystem_tools.py`
- [x] Walkthrough: `docs/steps/02-access-levels.md`
- [ ] User verified

Verify: `pytest` (103), `champion chat --level 1.1`, then the checks in the walkthrough.

Status: built and pushed (`545b1ea`); 103 tests pass on Databricks serverless; scripted chat checked (blocked read at 1.1, `/level 2.1` allows it, `/proc` blocked at every level, `/audit` rows). Write tools come with the agents that need them; the scope check for writing is already in place and tested. Merged afterwards on `main` by the user: configurable `llm.api_key_env` and a Responses API call in `champion doctor --online` (`6fcc27c`, `682ced7`, `447e011`), then one request per configured model (`llm.model_id` + `llm.model_ids`) in `champion doctor --online` (`6780ce6`); now 104 of 106 tests pass (two to fix in step 2a).

## Fast demo order (current plan)

Goal: a working multi-agent demo for the interview as soon as possible. Decided on 16 Nov 2026: one framework (OpenAI Agents SDK), steps ordered by demo value, everything on `main` until the demo. Work not needed for the demo keeps its design and moves to the later items L1-L7 in the full roadmap below.

| Order | Step | What | Why for the demo |
| --- | --- | --- | --- |
| done | 0-2 | Config, doctor, first agent, access levels, file tools, audit | Safety story |
| 1 | 2a | Small fixes | Clean test run |
| 2 | 3 | Orchestrator and specialist plug-in interface | Shows the multi-agent design |
| 3 | 4 | Linux specialist | Quick to build, easy to demo |
| 4 | 5 | PyDyna knowledge specialist on the existing knowledge base | Core CAE value |
| 5 | 6 | Textual TUI | Looks good in a demo |
| 6 | 7 | Large-model reading (index, outline, find, read range) | Shows the 1-8 GB handling |
| - | Demo ready | Final demo script, real-key run on the Linux machine | Interview |

### Step 2a - Small fixes
* Fix `tests/test_doctor.py::test_online_check_reports_error_type_only`: the config text has no `llm:` line, so `api_key_env` and `model_ids` are not applied (start the text with `llm:\n  model_id: test-model\n`).
* Fix `tests/test_config.py::test_example_config_matches_defaults`: `config/config.example.yaml` has `api_key_env: DATABRICKS_TOKEN` but the default in `config.py` is `OPENAI_API_KEY`; the user decides which one is right and both are aligned.
* Optional, if the user agrees: render final answers as Markdown in the plain chat with `rich` (plain text when output is not a terminal, `champion chat --plain`).
* REPORT.md: configurable `llm.api_key_env`, Responses API check in `champion doctor --online`.
- [ ] Not started

Verify: `pytest` (all pass).

### Step 3 - Orchestrator and specialist plug-in interface
`champion/specialists/base.py` (`SpecialistSpec`: name, description, instructions, tools, routing hints, enabled flag) and `registry.py`; orchestrator built from the enabled specialists with `Agent.as_tool`; `session_info` and the generic file tools stay on the orchestrator; contract tests with a dummy specialist (offered to the orchestrator, rejected if a tool has no policy entry); the chat shows which specialist was called. Learn: agents as tools, nested runs.
- [ ] Not started

Verify: `pytest`; `champion chat`, a question that the orchestrator passes to a specialist (`-> dummy_agent(...)`).

### Step 4 - Linux specialist (on `main`)
`linux_agent` with `process_list`, `disk_analysis` (largest folders and files inside the allowed area) and `shell_run` with command rules: a read-only allow-list (for example `ls`, `du`, `df`, `ps`, `grep`, `head`, `tail`, `wc`, `find`) runs at any level within the allowed paths; other commands need a write level and a yes/no approval in the chat; no shell operators or redirection outside the rules; timeout, output cap, audit. Skills and MCP for this agent come later (L1, L2).
- [ ] Not started

Verify: "Which folders use the most space here?", "Which processes use the most memory?", a write command asks for approval, a command outside the rules is blocked.

### Step 5 - PyDyna knowledge specialist (on `main`)
`pydyna_agent` with the knowledge tools ported from PyDyna_Agent_git (LS-DYNA keywords, PyDyna API, and `search_pydpf_post` if that collection exists), reading the existing Chroma `vector_db/` and JSONL `knowledge-base/` under `pydyna.knowledge_root`, read only. No `champion kb` build yet (L5); `champion doctor` checks that the collections exist. Needs: the path of the existing knowledge folder on the Linux machine. Learn: retrieval as tools, grounding answers.
- [ ] Not started

Verify: a keyword card question (for example the fields of `*MAT_024`) and a PyDyna code question, answered from the knowledge tools with sources.

### Step 6 - Textual TUI
`champion tui`: streamed answers rendered as Markdown, tool-event panel (orchestrator and specialist calls), status bar (model, level, tokens), level selector and `/level`, approval dialog (used by the Linux shell tool), audit view. Learn: `Runner.run_streamed` events.
- [ ] Not started

Verify: the step 3-5 questions in the TUI; an approval dialog for a shell command; level change from the selector.

### Step 7 - Large-model reading
Streamed keyword index (cached for files of 100 MiB or more), include-file tree, `model_outline`, `model_find`, `model_read_range` on the PyDyna specialist; hard limit `pydyna.max_model_bytes`. Editing comes later (L3).
- [ ] Not started

Verify: outline and find on a model over 1 GB; a second question on the same model uses the cached index.

### Demo ready
REPORT.md demo script final (about 10 minutes); full run on the Linux machine with the real key and real files; user verified.
- [ ] Not started

## Full roadmap (after the demo)

The original roadmap stays the reference for the work after the demo. Items already covered by the fast demo order are marked; the remaining items are numbered L1-L7 (later). Phase A builds the remaining shared parts on `main`, phase B builds agent work on branches, phase C integrates and hardens.

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

### Phase A - remaining shared foundations on `main` (after the demo)

Already covered: access levels, policy, file tools (step 2); specialist plug-in interface and orchestrator (fast demo step 3); Textual TUI (fast demo step 6); LS-DYNA keyword index and read tools (fast demo step 7). New features add their approval screens to the TUI.

#### L1 - Skills framework
Skill format (folder with `skill.yaml`, `instructions.md`, optional `snippets/` of tested code and optional `helpers.py`), registry, `list_skills` and `load_skill` tools offered only to compatible specialists, skills loaded on demand (not all in the prompt). Port mChampion's `cae.crash_loadcase` as the first skill. Learn: progressive disclosure, keeping prompts small.
- [ ] Not started

#### L2 - MCP framework
MCP client using the SDK's MCP server support (stdio first), `mcp.servers` config, per-server tool allowlist, every MCP tool mapped to a policy entry and approval rule, servers attached to specific specialists through their spec, clean start and stop. Test with a small local MCP server. Learn: MCP, treating external tools as untrusted.
- [ ] Not started

#### L3 - LS-DYNA model editing (shared library)
Builds on the keyword index and read tools of fast demo step 7: patch-edit engine (exact old text, streamed atomic apply, diff, reverse-patch undo, approval). Learn: streaming parsing, atomic replace, approval pause and resume.
- [ ] Not started

#### L4 - Code execution framework
`ExecutionBackend` interface, static-check interface (Python AST first), runtime guard for Python, approval flow, review records keyed by code hash, generic reviewer factory with typed verdict (policy and intent), `HostPythonBackend`. Learn: layered defence, audit hooks and their limits.
- [ ] Not started

#### L5 - Knowledge base command
`champion kb extract/build/status`, staging-collection swap, cosine distance, manifest; projects registered by specialists. Until then the PyDyna specialist reads the existing knowledge base as it is (fast demo step 5). Learn: Chroma build, safe replace.
- [ ] Not started

### Phase B - one branch per agent

Until the demo there are no branches: the Linux specialist (fast demo step 4) and the PyDyna knowledge specialist (fast demo step 5) are built on `main`. After the demo, each branch is created from `main` after the items it needs are merged, has its own sub-steps (one commit each, same rules as above), adds files only under its own package, config section and `docs/steps/`, and is merged through a GitHub pull request. PLAN.md and REPORT.md are updated on `main` at merge time.

| Order | Branch | Needs from phase A | Content |
| --- | --- | --- | --- |
| 1 | `agent/linux` | L1, L2 | Base agent built in fast demo step 4; on the branch: skills `linux.disk_analysis`, `hpc.job_submission`, MCP servers if needed |
| 2 | `agent/pydyna` | L1, L3-L5 | Knowledge tools and model reading built in fast demo steps 5 and 7; on the branch: `pydyna_count_nodes`, `pydyna_local_materials`, `pydyna_edit_model`, reviewer and `pydyna_execute` on the host backend; kb projects `pydyna`, `pydpf_post`; skills `cae.model_check`, PyDyna code templates |
| 3 | `agent/ansa-meta` | L1, L4, L5 | ANSA/META Python API specialist based on ANSA-META_CodeRAG: knowledge tools on `ansa_meta.knowledge_root`, ANSA and META batch backends (licensed batch runs), static check for ANSA/META scripts; kb projects `ansa_api`, `meta_api`; skills with ANSA/META script templates |
| 4 | `agent/primer` | L1, L3-L5 | Oasys PRIMER automation specialist: JavaScript API knowledge on `primer.knowledge_root`, PRIMER batch backend (licensed batch runs, JavaScript), JavaScript static check, reuse of the LS-DYNA library; kb project `primer_js`; skills with PRIMER script templates |

At most two branches are open at the same time; a branch can be reviewed while the next L item is built on `main`.

### Phase C - integration and hardening

#### L6 - Integration
All four specialists enabled together; cross-agent routing tests (one question per agent, mixed questions); cross-agent skills (for example model check across PyDyna and PRIMER); demo script updated.
- [ ] Not started

#### L7 - Hardening and final check
README, full test run, security review of every tool and MCP entry, confirm mChampion, PyDyna_Agent_git and ANSA-META_CodeRAG unchanged.
- [ ] Not started

## Skills: when and where
* The skills framework is built once in phase A (L1), before any agent branch, so agents never build it again.
* Skill content is written on each agent branch together with the agent, because a skill must match that agent's tools. Cross-agent skills are added in L6.
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
* The shared knowledge layer (read side in fast demo step 5, `champion kb` in L5) opens any root with this layout, checks it (`champion kb status`, `champion doctor`) and builds into it (`champion kb build`); agents only declare their collection names and JSONL files.
* Fast demo step 5 introduces `pydyna.knowledge_root` (instead of `pydyna.database_path` / `pydyna.keyword_reference_path`).

## MCP plan
* L2 builds the MCP client framework (after the demo). Each agent's MCP servers are added later on its branch or after phase C.
* Planned servers per agent: a knowledge-retrieval MCP server (the agent's knowledge base search exposed over MCP), web search, mail handling.
* Knowledge retrieval over MCP: knowledge tools are written as plain functions (as in step 1), so the same code can later be served by a Champion MCP server (`champion mcp serve-kb --agent pydyna`) without rewriting.
* Rules the framework enforces from L2:
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
* Fast demo order: finish a working demo for the interview as soon as possible; steps by demo value, everything on `main` until the demo. (User, in conversation, 16 Nov 2026.)
* One framework only (OpenAI Agents SDK); no LangGraph or CrewAI versions, to save time. (User, in conversation, 16 Nov 2026.)

## Open decisions
* `pydyna.max_model_bytes` value (8 GiB for now).
* Approval for writes inside scope at levels 1.2/2.2 (currently: only deletes and model edits ask).
* Which web search and mail MCP servers (provider, company approval for sending data out).
* Interview date (decides how much of the fast demo order is needed).
* Path of the existing PyDyna knowledge folder (`vector_db/`, `knowledge-base/`) on the Linux machine (needed for fast demo step 5).
* Markdown rendering in the plain chat now (step 2a) or only in the TUI (step 6).
