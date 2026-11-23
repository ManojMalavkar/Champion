# Track T8.1-T8.4 - Champion memory layers L0-L3

Branch `track/memory`. Library only (`champion/memory/`): nothing in the chat changes until core step M6. Plan: `docs/tracks/memory.md`. Built 21 Nov 2026.

## Files

| File | Layer | What it does |
| --- | --- | --- |
| `storage.py` | all | Atomic JSON (temp file, fsync, `os.replace`), mode 600 / folders 700, no symbolic links, secret filter (`refuse_secrets` for saved memory, `redact` for automatic records), IDs `PREFIX-YYYYMMDD-N` claimed with `O_EXCL`, real home `~/.champion/memory` |
| `runtime_state.py` | L0 | `RuntimeStateStore` per working folder (`.champion/runtime/REQ-*.json`); `RuntimeState` with steps, tool calls, approvals, pending action, sandbox and staged files, errors and retries, cancel; archive or delete at the end; `unfinished()` after a crash |
| `working.py` | L1 | `WorkingMemory` (task, pinned instructions, corrections, entities, evidence, assumptions, questions, approvals, `render()`); `select_history` / `make_session_callback` (last N turns, older tool outputs cut, token budget, dropped turns reported) |
| `summaries.py` | L2 | `ConversationSummary` + `SummaryStore` (`SUM-*.json`, statuses, supersede, project filter, `render_active`); `summarize` / `parse_summary` with the configured model |
| `preferences.py` | L3 | `PreferenceStore` (`preferences.json`), 15 known keys with types and choices, sources `user` / `agent_confirmed`, safety preferences only stricter |
| `separation.py` | rule | `classify`: preference, engineering fact, requirement, result, engineering change |

## How it works

### L0 runtime state
`store.start(agent=..., workflow=..., sandbox_path=...)` writes `REQ-20261121-0001.json`. Every change saves the file at once, so a crash leaves the last state. `request_approval` stores the question and the pending action (for example `replace_parameter` on `*MAT_024:120045`, SIGY 340 -> 360) and sets `awaiting_approval`; `applying` is refused while an approval is open; a "no" clears the pending action. `finish` moves the file to `archive/` without result excerpts (the archive keeps the last 50), or deletes it. Tool results are kept only as a 2000-character excerpt while the request runs; the audit log stays the full record (`audit_id` links them).

### L1 working memory
The raw conversation is not sent whole. `select_history` keeps the last 8 turns, cuts tool outputs of older turns to 2000 characters (the last 2 turns keep them whole), and drops the oldest turns until the estimate (characters / 4) fits 16000 tokens. Turns start at a user message, so a tool call always travels with its result. Dropped turns are reported unchanged, for L2. What must survive dropping (task, corrections, IDs, open approvals) lives in `WorkingMemory` and is rendered as a short block for the instructions.

### L2 summaries
`summarize(turns, call_model)` sends the turns (secrets removed, tool outputs shortened) with fixed instructions and expects JSON in the user's format (`decisions`, `constraints`, `paths`, `rules`, `architecture`, `open_items`, `rejected_alternatives`). Champion sets the ID, date, project and sessions; the model cannot set a status. Engineering statements go to `deferred` with their kind. Only `active` items are rendered.

### L3 preferences and separation
"Show force in kN" is saved (`display_units: {force: kN}`); "Section force must remain below 320 kN", "Program target is 300 kN" and "Job 784231 produced 318 kN" are refused with the layer they belong to. `destructive_action_confirmation` can only be true, `generated_code_policy` has no automatic-run choice, `allowed_project_roots` is a hint (access stays with `/access`).

## Tests (`tests/memory/`, 50)
* `test_memory_runtime_state.py` (11): file modes, the user's example flow, declined approval, sandbox escape (`..`, symbolic link), redaction, retries, archive without excerpts, delete, cancel, crash recovery, pruning, bad IDs, steps.
* `test_memory_working.py` (9): last turns, cutting, budget, call/result pairs, huge turn, SDK callback, settings, entities, render, secrets.
* `test_memory_summaries.py` (6): fake-model summary, refused answers, the user's `decision` key, supersede and project filter, store refusals, folders.
* `test_memory_preferences.py` (24 incl. parameters): the separation table, the user's example, agent alone refused, refused values, paths, broken file.

Result on Databricks serverless (copy in `/tmp/champ`): 50 memory tests pass; whole suite 319 passed (269 before). Extra check: `make_session_callback` is accepted by the SDK `RunConfig` and gives the expected selection on real `SQLiteSession` items.

## Not in this step
* Hook into runtime, orchestrator and chat, `/memory` commands, config section `memory` (M6, T8.8-T8.9).
* Engineering layers with evidence (T8.5 onwards), decisions D1-D5.

## Pending local checks (Linux, project installed)
* `python -m pytest tests/memory -q` passes.
* `ls -la ~/.champion` after a test run: nothing created in the real home (tests use temporary folders).
