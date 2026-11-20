# Track T8 - Memory

Branch `track/memory`, library `champion/memory/`, tests `tests/memory/`. Status: T8.1-T8.5 can start (library and tests only); T8.6 needs core step M6.

## Goal
Today each request sends the last `orchestration.history_turns` turns as raw messages; older turns are dropped and nothing is kept after the chat ends. Memory keeps what matters, in separate places, and sends only what is relevant.

## Where memory lives (P.8 decision 9)

| Store | Holds | Location |
| --- | --- | --- |
| Conversation memory | Preferences, previous work, current task, conversation summaries | Per user: `~/.champion/memory/` |
| Project memory | Decisions, engineering lessons, model structure, program structure | A folder the user selects (`/memory project <folder>`); none selected = nothing project-related saved |
| SOPs | Standard procedures | Skills (T5), P.8 decision 11 (may change) |

Model and program structure items hold a short summary plus the file path and SHA-256, so a changed file shows the item is out of date; the T6 index stays the real source.

## Who saves (P.8 decision 10; confirmed by the user, 20 Nov 2026)

| Kind | Saved by |
| --- | --- |
| Current task, previous work, conversation summary | Agent, by itself; shown in the turn summary |
| Model and program structure | Agent, by itself (summary + path + hash) |
| Decisions, engineering lessons | Agent proposes, user confirms |
| Preferences | User, or agent proposes and user confirms |
| Anything | User with `/memory add`, edit, forget |

Every item records its source (user, agent, file) and date. Secrets are never saved (same rules as sensitive files). A memory item never starts an edit or a command by itself. Memory files follow the access rules.

## Sub-steps
- [ ] T8.1 Store: item format, the two roots, read, write, edit, forget; secret filter. Verify: tests for each store and for a refused secret.
- [ ] T8.2 Compaction: summary of older turns (decisions, current task, previous work) with the configured model. Verify: fake-model test keeps decisions from turn 1 after 20 turns.
- [ ] T8.3 Retrieval: relevant items per question (keywords first; embeddings after M1).
- [ ] T8.4 Save rules from the table above (split confirmed). Verify: agent-saved kinds appear in the turn summary; proposed kinds are saved only after a yes; a no saves nothing.
- [ ] T8.5 `/memory` command logic: list, show, add, edit, forget, project.
- [ ] T8.6 Integration through M6 (core request CR).
- [ ] T8.7 SOPs as skills (needs T5).

## Decisions
* 9-11 in PLAN.md P.8 (user, 20 Nov 2026).

## Core requests (CR)
* M6 memory hook in the runtime, orchestrator and chat (PLAN.md P.3), after T8.1-T8.3.

## Findings and live checks
None yet.
