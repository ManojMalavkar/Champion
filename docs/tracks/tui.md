# Track T9 - TUI

Branch `track/tui`, package `champion/tui/`, tests `tests/tui/`. Status: on 21 Nov 2026 the user's "mChampion TUI Development Plan" replaced the first T9 plan. It is adopted below and fitted to the existing Champion code (what exists, what is missing, where each missing piece is built). Milestone 1 started 21 Nov 2026: T9.1 built (not committed), 11 TUI tests, full suite 280 passed on serverless; logo files pushed by the user (4004ad8); walkthrough `docs/steps/t9-1-tui-shell.md`. Next: T9.2.

## Goal (user's plan, sections 1-2)
A safe, observable, engineering-focused Textual TUI: one workspace for conversation, settings, path access, file-change audit, past sessions, HPC jobs, agent-owned engineering software and, later, working-vs-base model comparison. The plain chat (`champion chat`) stays.

Principles:
1. Conversation first: the conversation is always the largest area.
2. Observable execution: agent and tool actions are visible while they run, then collapse.
3. Explicit access: the user picks None, Read or Edit for every path.
4. Safe defaults: Working and Base get no access until the user assigns it; Read only is suggested for Base.
5. One way to pick a source: Working and Base can each come from a JobID or a location.
6. Resolved context: agents get resolved paths, whichever way the source was picked.
7. Agent ownership: only software started and tracked by Champion agents is shown.
8. Complete file audit: every file event, with time, agent, status and path.
9. Separation: no agent, filesystem, HPC or permission logic in TUI widgets. The TUI only shows state and events from the core.
10. Small steps: a stable base first, then the specialist features.

## Layout (user's plan, section 3)
```text
+----------------------------------------------------------------------------------------------+
| [M] mChampion                                                                                |
| Working | [JobID v] 458721 | /CAE/M250/FFB/run_042      | [x Read] [x Edit]                  |
| Base    | [Path  v]        | /CAE/M250/reference/v22    | [x Read] [  Edit]                  |
+----------------------------------------------------------------------------------------------+
| Conversation | Settings | Paths & Access | Modified Files | Session History | HPC Jobs       |
+----------------------------------------------------------------------+-----------------------+
|                                                                      | LIVE AGENT USAGE      |
|                         ACTIVE TAB                                   | AGENT SOFTWARE        |
|                                                                      |   RAM / Disk          |
|                                                                      | CONTEXT               |
|                                                                      |   Files affected      |
|                                                                      |   Read / Edit paths   |
+----------------------------------------------------------------------+-----------------------+
| LLM: <model> | Total Tokens: 42,810 | Current Time: 22:40:12                                 |
+----------------------------------------------------------------------------------------------+
```
* The conversation keeps the largest area. The right panel has a minimum width (setting, about 24-30 columns) and can be hidden or opened as a drawer on narrow terminals.
* If the terminal is too small, a minimum-size message is shown instead of a broken layout.
* Logo `M` states: Idle (static), Thinking (slow pulse: planning, routing, waiting for the model), Working (fast pulse: an agent or tool runs), Error (red: the request ended with an unresolved failure). There is a reduced-motion setting. If the terminal cannot show it, a styled text `M` is used.

## Fit with the existing code (read 21 Nov 2026)
| Plan item | Exists today | Missing | Built in |
| --- | --- | --- | --- |
| Conversation turn | `ChampionRuntime.ask(text, on_tool_event)`, `Runner.run` (not streamed), `TurnResult(text, tool_events, usage)` | streamed text; "waiting for model" and agent start/end events (for Thinking vs Working and the agent panel) | CR-T9-3 on `main` |
| Intermediate actions | `ToolEventRecorder` / `SpecialistHooks`: `ToolEvent(phase, agent, tool, arguments, result_preview, result_chars, seconds)`, sub-agents as `Group/Member` | grouping per request | TUI |
| Approval | `ChampionContext.approver` is a blocking `callable(ApprovalRequest) -> bool`; `context.declined` | modal dialog | TUI (thread worker, see Design) |
| None / Read / Edit access | `policy/access.py` `AccessLevel(working, outside, folders[none/read/edit])`, `with_folder`, `with_rights`, `check_folder`, `is_wide`; `context.set_access` writes an audit row; `/access save` makes it persistent; every tool checks `context.level` when it is called, so a revoke applies from the next tool call | table UI | TUI, using the core functions only |
| Working / Base source | nothing; `execution.working_directory` is fixed at start | `ModelContext(role, source_type, source_value, job_id, resolved_path, master_file, access)` and passing it to agents | CR-T9-4 on `main`; JobID resolution from T2 HPC |
| Modified Files | `changes` table in the audit DB: `ts, agent, tool, action, path, new_path, session_id, undo`; actions `created, modified, moved, renamed, copied, permissions, deleted, compressed, extracted, undone`; rows stay when a file is deleted | `request_id`; actions `generated, exported, restored, overwritten` (added when a tool needs them); unique-file count | CR-T9-5 on `main` |
| Session History | `session_id` on every audit row | `sessions` table (start, close, Working path, source type, JobID) | CR-T9-6 on `main`, aligned with T8 memory (L0/L2) |
| HPC Jobs | nothing on `main` | job list, status, JobID -> path | T2 HPC track (API for the TUI) |
| Agent Software | nothing | process registry (`process_id, application, pid, owner_agent, session_id, launch_time, working_directory, status, exit_code`) | CR-T9-7 on `main` (used by M3, T3 ANSA/META, T4 PRIMER) |
| RAM / Disk | nothing | host RAM; free disk of the filesystem holding the Working path | TUI widget (the TUI reads host data itself; this is not an agent tool, so the `/proc` block for agents does not apply) |
| Footer tokens | `runtime.total_usage` (requests, input, output, total) | cache-read and cache-write tokens when the provider returns them | CR-T9-3 |
| Settings | config is read only for agents; no temperature, max output tokens or streaming keys | session overrides (never written to `config.yaml`), new keys | CR-T9-8 on `main` |
| Slash commands | dispatch written inline in `chat_loop` | reusable `run_command` | CR-T9-1 (later: the plan makes them optional) |
| `champion tui` | nothing | subcommand | CR-T9-2 on `main` |
| Working vs Base comparison | nothing | comparison tools | T1 PyDyna (not this track); the TUI only shows both contexts |

## Design
* **Threading.** The Textual app owns the main asyncio loop. Each turn runs in a Textual thread worker (`@work(thread=True, exclusive=True)`) with its own `asyncio.run(runtime.ask(...))`. Then a blocking approver is fine: it posts the dialog with `app.call_from_thread(...)` and waits on a `threading.Event` for the answer. No async approval hook is needed on `main` (the CR in the old plan is dropped if T9.3 confirms this).
* **Tool events** are passed to the UI with `call_from_thread` (Textual widgets are not thread safe).
* **One turn at a time.** While a turn runs, the input is disabled and shows "working... (Esc to cancel)"; text typed then is not kept, and it can never answer a dialog (the dialog has its own focus and buttons, default No). Same rule as the 4d.8 chat fix.
* **Paste** = one message: the input is a multi-line `TextArea` (Enter sends, Shift+Enter or a paste adds lines); slash commands run only from a single typed line, as in the chat.
* **Cancel**: Esc asks the worker to stop; the turn ends with "Stopped by the user" (checked between tool calls; a running tool is not killed).
* **Same behaviour as the chat**: texts for errors, the per-turn change summary and the undo report come from the core and chat functions (`describe_error`, `turn_summary`, `changes_text`, `undo_command`); the TUI only lays them out.
* **Conversation**: user text right-aligned in an accent colour, Champion answers left-aligned (Markdown), no speaker labels. Enter sends, Shift+Enter adds a new line, Up/Down recalls history when the input is empty, Ctrl+C cancels the running request (explicit binding; Ctrl+Q quits).
* **Only observable events** are shown (agent chosen, tool calls, files, approvals, errors), never hidden reasoning.
* **No context switch during a turn**: the Working/Base selectors and access changes that widen access are locked while a turn runs. Revoking stays possible and applies from the next tool call.
* **No mock data in normal runs**: a tab whose source is not built yet says "not available yet (needs ...)". Mock data only in tests, clearly marked.
* **TUI state**: `champion/tui/state.py` holds `ChampionUIState` as a view model, filled only from the runtime, the context and the events; it has no rules of its own.

## Package layout (fitted to the existing code)
The user's section 10 tree is kept for the TUI part. Core parts go to the existing core modules on `main` through core requests, because tracks do not edit shared files and `cli.py`, `core/`, `policy/` and `storage/` already exist:
```text
champion/tui/
  __init__.py  __main__.py  app.py  state.py  messages.py  bindings.py  theme.tcss  worker.py  approval.py
  screens/   conversation.py settings.py paths_access.py modified_files.py session_history.py hpc_jobs.py
  widgets/   logo_state.py model_source_selector.py access_selector.py conversation_view.py
             intermediate_actions.py agent_usage.py agent_software.py resource_usage.py
             context_summary.py footer.py
  modals/    approval.py select_job.py select_location.py error_details.py confirm.py
```
The user's `runtime/`, `context/`, `audit/` and `hpc/` become CR-T9-3 to CR-T9-8 in `champion/core/`, `champion/policy/` and `champion/storage/`, and the T2 package.

## Sub-steps
Milestone 1 (user's section 17 = phases 1 and 2): T9.1-T9.5. It uses only what exists today.
- [x] **T9.1 Shell (phase 1)** (built 21 Nov 2026; logo files `m_logo.png` / `m_logo.gif` go into `champion/tui/assets/`, text-M fallback until then; Linux check pending): app, header (Working/Base rows show "not selected"), six tabs, right panel, footer (real model name, `runtime.total_usage.total_tokens`, clock updated every second, clock widget only), minimum-size message, right-panel toggle, `ChampionUIState`, `python -m champion.tui` with the same start options as `champion chat`. Verify: pilot tests for switching tabs without losing state, the minimum-size message at 60x20, the footer model and token values, and the clock changing while the other widgets do not redraw.
- [ ] **T9.2 Logo states (phase 1)**: text-art `M` with Idle/Thinking/Working/Error styles plus a reduced-motion flag. Until CR-T9-3: Thinking = turn running and no tool open; Working = at least one tool open (from start/end events); Error = the turn raised. Verify: pilot test runs a fake-model turn through Idle -> Thinking -> Working -> Idle, and an error turn -> Error.
- [ ] **T9.3 Conversation (phase 2)**: alignment and colours, multi-line input, history, the turn in a thread worker, Ctrl+C cancel, errors and the max-turns stop shown as in the chat. Verify: pilot test with `tests/fake_model.py`: question right, answer left as Markdown, no labels; Ctrl+C during a slow fake turn ends it and the app stays usable.
- [ ] **T9.4 Intermediate actions and approval (phase 2)**: one collapsible block per request, live lines from `ToolEvent` (sub-agents indented, BLOCKED/ERROR in red). On success it collapses to `Completed - Linux + PyDyna - 7 tool calls - 4.2 s`; on failure it stays open. Large results show only the preview and size. Approval modal: Yes/No, focus starts on No, Esc = No, keys typed before it do not answer it. Verify: pilot tests for collapse, failure staying open, three indent levels, yes and no (no ends the turn with the fixed answer and is not asked again).
- [ ] **T9.5 Right panel, real data available today (phase 5 part)**: Live Agent Usage (orchestrator and specialists from `context.specialists` / `sub_agents`, state from the events: idle / running / waiting for approval / completed / error), Context (Files affected = unique paths in this session's changes; Read paths and Edit paths from `context.level`, Edit counted in Read), RAM (host) and Disk (filesystem of the Working path, else the working directory, labelled) with warning above 85% RAM or below 10% free disk and critical above 95% or below 5% (settings). Agent Software shows "no agent-started software" until CR-T9-7. Verify: pilot tests: two edits of one file give Files affected 1; adding an edit folder changes both counts; thresholds change the colour.
- [ ] **T9.6 Paths & Access tab (phase 3 part)**: `Path | None | Read | Edit` table with rows for the working folder, every folder in `context.level.folders` and "everything else". Rules: None clears Read and Edit; Read clears None; Edit also ticks Read; clearing Read clears Edit; widening asks for confirmation (and `is_wide` folders get the same wide-edit confirmation as `/access`). Changes go through `check_folder` / `with_folder` / `with_rights` / `context.set_access`, so there is one audit row per change. "Keep for next start" = `/access save`. Add and remove path. Colours: gray, green, orange. Verify: unit tests of the checkbox rules; a pilot test sets a folder to None and a fake read tool is then BLOCKED; set to Read, then an edit is BLOCKED.
- [ ] **T9.7 Modified Files tab (phase 4 part)**: `Time | Agent | Status | File`, newest first, from the audit `changes` rows of this session; rename/move shows `old -> new`; date shown when it is not today; an Undo action through `undo_command`. Filters, diff and opening the folder come later. Verify: a pilot test after fake create, edit and rename shows three rows in order, and the rename shows both paths.
- [ ] **T9.8 Working/Base selection (phase 3)**: needs CR-T9-4. Location first (folder browser, typed path, recent paths, normalised and checked to exist, no access given); JobID after T2 (search, recent, running, completed, typed JobID, status and resolved path shown before choosing). Base can also be None. After a source is chosen, access is asked with the T9.6 rules, with Read suggested for Base. Header shows source, path and access colours. Verify: pilot test: picking a location gives no access until the user ticks; the agents receive a `ModelContext` with the resolved path.
- [ ] **T9.9 HPC Jobs tab (phase 6)**: needs the T2 API. Table `Job ID | Model | Loadcase | Status | Runtime`, refresh, search, select as Working or as Base, details. Kill, delete and resubmit are never row shortcuts; they go through policy and confirmation. Verify: a fake job source fills the table; select as Working resolves the path.
- [ ] **T9.10 Session History tab (phase 7)**: needs CR-T9-6. Closed sessions only, newest first, `Date | Time | Working Path` (source type and JobID kept). Reopening a session comes later. Verify: two closed fake sessions shown in order, the current session not shown.
- [ ] **T9.11 Agent Software (phase 5 rest)**: needs CR-T9-7. Only registered processes, numbered instances (`ANSA #1 PID 8421`); a dead PID is marked stopped or unknown. Verify: a registered fake process is shown; a process not registered is never shown.
- [ ] **T9.12 Settings tab**: needs CR-T9-8 for the LLM and safety keys. Groups: LLM, Champion, Safety, Logging, with app defaults and session overrides; reduced motion and theme first (TUI only). `config.yaml` is never written. Verify: a session override is used in the next turn and is gone after restart.
- [ ] **T9.13 Command, docs and local check**: `champion tui` (CR-T9-2), `docs/steps/t9-tui.md`, README section, status line for PLAN.md P.7 (edited on `main`).

## Tests
* `tests/tui/` only; Textual pilot (`run_test`, headless, fixed size 120x40); `tests/fake_model.py` scripted model; no network.
* `pytest.importorskip("textual")` at the top of each TUI test file, so core suites stay green where Textual is missing (Databricks serverless: install `textual` first, see memory note).
* No edits to shared test files; if the CR-1 conftest fixture is needed for agents, reuse it from `main`.
* Safety tests (user's section 15) are added in the sub-step that makes each one possible: reading a None path (T9.6), editing a Read path (T9.6), editing Base without Edit (T9.8), killing a process that is not owned (T9.11), switching Working during an edit (T9.8), job control without a JobID (T9.9).

## Pending local checks (Linux, user)
1. `PYTHONPATH=<Champion-tui worktree> python -m champion.tui` opens; the real model answers on the left, the question is on the right.
2. Ask for a delete -> dialog -> No -> fixed answer; again -> Yes -> done; row in Modified Files; Undo.
3. Paste 20 lines -> one message.
4. Resize the terminal down to the minimum; toggle the right panel; Ctrl+C during a long turn.
5. Over SSH and in the terminal used for the demo (colours, mouse, logo pulse, reduced motion).

## Decisions
* The plain chat (`champion chat`) stays; the TUI is an extra front end.
* No logic is copied from `chat.py`: if a function is missing or not reusable, write a core request instead of a second version.
* 21 Nov 2026 (user): the "mChampion TUI Development Plan" is the TUI plan; the first milestone is phases 1 and 2; slash commands are optional and come later.
* Access words in the UI are None / Read / Edit (never "write").

## Core requests (CR) on `main`
* CR-T9-1 (later): `chat.run_command(runtime, line, confirm)` and `chat.banner_lines(runtime)` taken out of `chat_loop`, behaviour unchanged.
* CR-T9-2: `champion tui` subcommand in `cli.py` (T9.13).
* CR-T9-3: streamed turn (`Runner.run_streamed`) and events for model wait, agent start and end, text deltas and cancel; cache token counts in `TurnUsage` (T9.2/T9.3 improve with it, milestone 1 works without it).
* CR-T9-4: `ModelContext` for Working and Base, held on `ChampionContext`, given to the agents' instructions and tools; choosing a source never changes `context.level` (T9.8).
* CR-T9-5: `request_id` on audit and change rows; `affected_files()` helper in `tools/changes.py`; new actions (`generated, exported, restored, overwritten`) when the first tool needs them (T9.7).
* CR-T9-6: `sessions` table (start, close, Working source and path, JobID), closed on exit, aligned with T8 (T9.10).
* CR-T9-7: process registry for software started by agents, with ownership and PID checks before any stop (T9.11).
* CR-T9-8: settings keys (temperature, max output tokens, streaming, confirm flags, thresholds) and session overrides that are never written to `config.yaml` (T9.12).
* Dropped unless T9.4 shows otherwise: async approval hook (the thread worker lets the existing blocking approver work).

## Other tracks this track waits for
* T2 HPC: job list, status and JobID -> path API (T9.8 JobID, T9.9).
* T8 memory: session summaries for later session reopening (after T9.10).
* T1 PyDyna: Working vs Base comparison tools (user's phase 8); the TUI only sends both contexts.

## Open questions for the user
1. Name shown in the UI: "mChampion" (as in the plan) or "Champion"?
2. (answered 21 Nov 2026: use the user's PNG and GIF; text M only as the fallback)
3. Milestone 1 = T9.1-T9.5 (including the right panel with today's real data), then T9.6-T9.7, which need no core request: OK?
4. CR-T9-4 (`ModelContext`) and CR-T9-3 (streaming) on `main`: start them in the main thread now, in parallel with milestone 1?

## Findings and live checks
* T9.1: logo files pushed by the user (4004ad8). PNG 1254x1254 with a transparent background, drawn at 12x5 cells (10x4 lost the M's shape; header now 6 rows). GIF 128x128, 63 frames x 40 ms, looping, frame 0 empty: T9.2 must skip empty frames. Pillow is used if present but is not a dependency; packaging the assets for a non-editable install needs `package-data` in `pyproject.toml` (core request, later).
