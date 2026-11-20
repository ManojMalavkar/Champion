# Track T3 - ANSA/META

Branch `track/ansa-meta`, package `champion/specialists/ansa_meta/`, tests `tests/ansa_meta/`, knowledge `knowledge/ansa_meta/`. Status: not started (waits for M1).

## Goal
One group `ansa_meta_agent` with sub-agents (user, 20 Nov 2026): Code (writes ANSA and META Python scripts) and Task (runs tested scripts in batch to extract information or do jobs). Knowledge from ANSA-META_CodeRAG (read only there).

## Sub-steps
- [ ] T3.1 Knowledge (needs M1): `ansa_api`, `meta_api` collections and the knowledge graph as search tools.
- [ ] T3.2 Code sub-agent: writes scripts; static check (forbidden modules and calls). Verify: a script passes the check, no run.
- [ ] T3.3 Batch backends (needs M2/M3 and the executables and licences, PLAN P.8 decision 5): ANSA `-nogui -execscript`, META batch.
- [ ] T3.4 Task sub-agent: parts, thickness, mass, materials; META curves and reports into a run folder. Verify: part list of a small model.
- [ ] T3.5 Skills (needs T5).

## Decisions
* Executables and licence environment: not yet clear (open).

## Core requests (CR)
None yet.

## Findings and live checks
None yet.
