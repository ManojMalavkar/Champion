# Track T8 - Memory

Branch `track/memory`, library `champion/memory/`, tests `tests/memory/`. Status 21 Nov 2026: plan revised with the user's layered CAE memory design (Copilot review, pasted by the user). The user chose to build the Champion memory layers L0-L3 first: T8.1-T8.4 built 21 Nov 2026 (library and 50 tests, whole suite 319 pass on serverless; walkthrough `docs/steps/t8-memory-l0-l3.md`; not committed yet). The engineering records (T8.5 onwards) wait for decisions D1-D5. T8.9 needs core step M6.

## Goal
Today each request sends the last `orchestration.history_turns` turns as raw messages; older turns are dropped and nothing is kept after the chat ends. Memory keeps what matters, in separate places, and sends only what is relevant.

## Where memory lives (P.8 decision 9)

| Store | Holds | Location |
| --- | --- | --- |
| Conversation memory | Preferences, previous work, current task, conversation summaries | Per user: `~/.champion/memory/` |
| Project memory | Decisions, engineering lessons, model structure, program structure | A folder the user selects (`/memory project <folder>`); none selected = nothing project-related saved |
| SOPs | Standard procedures | Skills (T5), P.8 decision 11 (may change) |

Model and program structure items hold a short summary plus the file path and SHA-256, so a changed file shows the item is out of date; the T6 index stays the real source.

## Champion memory layers L0-L3 (user, 21 Nov 2026; built first)

| Layer | What | Holds | Storage | Lifetime |
| --- | --- | --- | --- | --- |
| L0 Runtime execution state | Control of the running request, not knowledge | Request ID, agent and sub-agent, workflow, current step, tool calls (arguments + short result excerpt), pending approvals and pending action, access scope, sandbox path and staged files, errors and retries, cancellation | `<working folder>/.champion/runtime/<REQ-id>.json` (later: in memory, Redis, SQLite) | Deleted or archived (excerpts removed) when the request ends; unfinished files after a crash are listed |
| L1 Current conversation (working memory) | What the next model call needs | Active task, pinned user instructions and corrections, selected entities (paths, model, part, material, keyword and job IDs), retrieved evidence, assumptions, open questions, pending approvals; selection of the last relevant turns | In memory, per session | One chat session |
| L2 Conversation summaries | Completed parts of long conversations | Decisions with reasons, constraints, paths set by the user, naming and command rules, architecture choices, open items, rejected alternatives; source sessions | JSON per summary: project folder if selected, else `~/.champion/memory/summaries/` | Until superseded |
| L3 User preferences | Stable interaction and execution preferences | Units, result and report format, solver and precision, working locations, HPC gateway, naming, confirmation rules, generated-code policy, project roots, aliases | `~/.champion/memory/preferences.json` | Until the user changes them |

L1 limits (configurable later through M6): last 8 turns (6-12), token budget 16000 (8000-20000 by model), tool outputs of older turns cut to 2000 characters, never a tool call without its result.

Separation rule: a preference, an engineering fact, a requirement and a simulation result are never stored together (different authorities and lifecycles):

| Statement | Layer |
| --- | --- |
| "Show force in kN" | Preference (L3) |
| "Section force must remain below 320 kN" | Engineering fact (later: engineering knowledge, with evidence) |
| "Program target is 300 kN" | Requirement (later: `requirements`, versioned, approved) |
| "Job 784231 produced 318 kN" | Simulation result (later: `results_kpis`, with JobID and source) |

L3 refuses the last three; L2 keeps them apart as "deferred engineering statements", never as decisions. Safety preferences can only make Champion stricter: confirmation before destructive actions cannot be switched off, generated code is never run automatically, project roots in preferences do not grant access (`/access` does).

## Layered CAE memory (revision 21 Nov 2026)

Main rule: Champion never remembers a CAE conclusion as plain text without evidence. A trusted record links:
statement -> context (program, variant, release, load case) -> model version + JobID -> source files / result channels (path + SHA-256) -> extraction method (script + version) -> engineer validation and approval.

The eleven layers of the design and where each one is built:

| # | Layer | Built in | How |
| --- | --- | --- | --- |
| 1 | Current task memory | T8 | Task card per session (objective, context, master file, target IDs, files read, assumptions, pending questions and approvals, proposed changes); summarised into previous work when the task closes |
| 2 | Job and simulation memory | T8 table, filled by T2 | `simulation_jobs` + job events (status, solver, version, SMP/MPP, precision, CPUs, queue, folders, model hash, parent/restart job, wall time, FEMZIP, errors). T2 writes, T8 owns the schema (CR to T2) |
| 3 | Model identity and provenance | T8 table, data from T6 | `models` + `model_files` (master `.k`, include tree, SHA-256, units, solver version, baseline/ancestor). Identity = Program + Variant + Release + Loadcase + ModelVersion (+ JobID for a run) |
| 4 | LS-DYNA model knowledge | T6 (not T8) | T6 index and PART -> SECTION -> MAT graph stay the source; memory keeps only model summaries with path + hash |
| 5 | Results and KPIs | T8 | `results_kpis`: value, unit, signal/source, channel or entity ID, filter class, time window, extraction script + version, result file path + SHA-256, limit, validation status. Binary results never stored |
| 6 | Requirements | T8 (later sub-step) | `requirements`, versioned (revision, effective date, applicability, owner, status); only `approved` ones used as limits; never mixed across program or revision |
| 7 | Test correlation | Later (after T3 META) | `correlations`: test ID, matched JobID, channels, filtering, score, differences, agreed updates, approval |
| 8 | Engineering decisions | T8 | `decisions`: observation, evidence, change, reason, model + files + entities changed, outcome, accepted/rejected/reverted, where valid |
| 9 | Lessons at four levels | T8 | scope `shared`, `program`, `loadcase`, `local`; local never becomes shared by itself |
| 10 | Tool and API knowledge | M1 + T1/T3/T2 knowledge DBs | Not T8 |
| 11 | Execution and audit | Exists (`storage/audit.py`, `changes`) | Memory records link to audit rows and change entries by id; no second audit log |

Not in T8 (decided by the design itself): Neo4j, PostgreSQL, SharePoint, vector index (M1), secret manager. SQLite first; the schema stays plain SQL so a move to PostgreSQL for several users is a later step.

### Common fields of every record
`id`, `kind`, `scope`, `context` (program, variant, release, loadcase, model_version, job_id; any may be empty), `text`, `evidence` (list of {kind: file|job|kpi|audit|change|user, ref, sha256}), `source`, `source_version`, `created_by` (user / agent / tool), `created_at`, `status`, `approved_by`, `confidence`, `supersedes_id`.

Status lifecycle: `proposed -> reviewed -> validated -> approved -> superseded` (also `rejected`). Only `approved` items are given to the model as facts; others are given marked as unconfirmed, or not at all (D4).

### Context guard
Retrieval filters by the current task context. An item from another release, variant or load case is never given as a fact for this one; if it is shown, it is labelled with its own context. Highest-risk failure mode in the design: mixing releases.

## Who saves (P.8 decision 10; proposed split, confirm in T8.4)

| Kind | Saved by |
| --- | --- |
| Current task, previous work, conversation summary | Agent, by itself; shown in the turn summary |
| Model and program structure | Agent, by itself (summary + path + hash) |
| Decisions, engineering lessons | Agent proposes, user confirms |
| Preferences | User, or agent proposes and user confirms |
| Anything | User with `/memory add`, edit, forget |

Every item records its source (user, agent, file) and date. Secrets are never saved (same rules as sensitive files). A memory item never starts an edit or a command by itself. Memory files follow the access rules.

## Sub-steps
- [x] T8.1 L0 runtime state (`champion/memory/runtime_state.py`). Verify: start/save/load; steps after the end refused; applying refused while an approval is open; declined approval clears the pending action; staged file outside the sandbox refused; secrets in tool arguments redacted; finish archives without result excerpts, or deletes; unfinished states found after a crash; archive pruned; bad request IDs refused.
- [x] T8.2 L1 working memory (`champion/memory/working.py`). Verify: last N turns kept; older tool outputs cut, recent ones kept; token budget drops the oldest turns; a tool call never separated from its result; input not changed; entities found in text (paths, part/material/job IDs, keyword IDs); render stays under its limit.
- [x] T8.3 L2 summaries (`champion/memory/summaries.py`) with compaction through the configured model (fake model in tests). Verify: model JSON parsed (also in a code fence), engineering statements deferred, secrets refused, bad JSON refused, active decisions across summaries, supersede, project filter.
- [x] T8.4 L3 preferences (`champion/memory/preferences.py`) and the separation check (`champion/memory/separation.py`). Verify: the four statements above classified; preference set/get/forget; agent alone cannot set a preference; unknown keys, bad choices, secrets refused; safety preferences cannot weaken safety.
- [ ] T8.5 Record store for engineering layers: common fields, context, evidence, status lifecycle, supersede (storage per D1).
- [ ] T8.6 Engineering tables: `projects`, `models`, `model_files`, `simulation_jobs` (+ job events), `results_kpis`, `decisions`, `lessons`. Verify: a decision is refused as `approved` without evidence; a KPI without unit, source or extraction version is refused; model identity unique per Program+Variant+Release+Loadcase+ModelVersion; a changed file hash marks linked items out of date.
- [ ] T8.7 Retrieval with the context guard (keywords first; embeddings after M1). Verify: an R06 item is not returned as a fact for an R07 question; out-of-date items flagged.
- [ ] T8.8 Save rules (table above) + lifecycle transitions; `/memory` command logic: list, show, add, edit, forget, approve, reject, project, task.
- [ ] T8.9 Integration through M6 (core request CR): L0 written by the runtime and tool wrapper, L1 as the session input callback (replaces `keep_last_turns`) plus a working-memory block in the instructions, L2 compaction of dropped turns, L3 block in the instructions; config section `memory`. CR to T2 (write job records), CR to T6 (model identity + include tree with hashes).
- [ ] T8.10 Requirements table (versioned, approved only).
- [ ] T8.11 SOPs as skills (needs T5).
- Later: correlation memory (after T3 META), shared organisation-level store, PostgreSQL.

## Decisions
* 9-11 in PLAN.md P.8 (user, 20 Nov 2026).
* 21 Nov 2026: layered CAE memory design (evidence-linked records, context guard, status lifecycle, four knowledge levels) taken as the basis (user's Copilot review). PLAN.md P.4 T8 lines to be updated on `main` (tracks never edit PLAN.md).

## Open decisions (ask the user before T8.5; L0-L3 do not depend on them)
* D1 Storage: SQLite per root (proposed) instead of Markdown/JSONL files.
* D2 Scopes now: `local` (user) and `program`/`loadcase` inside the selected project folder; `shared` later, needs a controlled location.
* D3 Who may approve: the user in the chat (`/memory approve`) for now; no second reviewer.
* D4 Unapproved items: given to the model marked "unconfirmed" (proposed) or not at all.
* D5 Task context: the agent fills program/variant/release/loadcase from the model path and asks when unsure (proposed), or the user sets it with `/memory task`.

## Core requests (CR)
* M6 memory hook in the runtime, orchestrator and chat (PLAN.md P.3), after T8.1-T8.4 (details in T8.9).

## Findings and live checks
* 21 Nov 2026: "Increase gauge from 1.6 mm to 1.8 mm" was first classified as a preference; added the kind `engineering_change` so design changes never become L2 decisions or L3 preferences.
* The separation check is a broad guard: a software decision with a value and a unit plus a limit word ("each pass must finish within 120 s") goes to `deferred`, not lost.
* Pending local checks: listed in `docs/steps/t8-memory-l0-l3.md`.
