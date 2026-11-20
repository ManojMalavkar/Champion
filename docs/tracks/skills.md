# Track T5 - Skills

Branch `track/skills`, package `champion/skills/`, tests `tests/skills/`. Status: can start now (M0 done).

## Goal
Skills framework (former L1) that the other tracks write their skills on: instructions and code snippets loaded on demand by the agents named in the skill. Skills never run anything themselves.

## Sub-steps
- [ ] T5.1 Framework: skill folder (`skill.yaml`, `instructions.md`, optional `snippets/`, `helpers.py`), registry, `list_skills` and `load_skill` offered only to the named specialists. Verify: a test skill is listed and loaded by its agent only.
- [ ] T5.2 Checks: `champion skills list` and `champion skills check` (format, names, snippets compile).
- [ ] T5.3 First skills: `cae.crash_loadcase` (from mChampion), `linux.disk_analysis`.
- [ ] T5.4 Authoring guide `docs/skills.md` for the other tracks.

## Decisions
* Adding `list_skills` / `load_skill` to agents and the `champion skills` command touch shared files: expected core requests for T5.1 and T5.2.

## Core requests (CR)
None yet.

## Findings and live checks
None yet.
