# Track T1 - PyDyna

Branch `track/pydyna`, package `champion/specialists/pydyna/`, tests `tests/pydyna/`, knowledge `knowledge/pydyna/`. Status: not started (waits for M1).

## Goal
One group `pydyna_agent` with 3 sub-agents (user, 20 Nov 2026): Keyword Lookup, PyDyna Code, PyDPF-Post. Tools ported from PyDyna_Agent (read only there).

## Sub-steps
- [ ] T1.1 Keyword Lookup (needs M1): `search_keyword`, `lookup_keyword`, `get_material_model`, `get_contact_type`, `validate_keyword_params`. Verify: fields of `*MAT_024` with sources.
- [ ] T1.2 PyDyna Code (needs M1): `search_pydyna_api`, templates; writes scripts into the working folder, no run. Verify: a deck-building script passes `code_check`.
- [ ] T1.3 PyDPF-Post (needs M1): `pydpf_post` collection, d3plot/binout scripts. Verify: a section-force or energy curve script.
- [ ] T1.4 Run with approval (needs M3): host Python backend in a run folder.
- [ ] T1.5 Model questions (needs T6).
- [ ] T1.6 Skills (needs T5).

## Decisions
* Group of 3 sub-agents; database in `knowledge/pydyna/` (JSONL committed, `vector_db/` built locally).
* The built-in `pydyna` config section still lives in `config.py` `DEFAULTS`; moving it into this package is a core request when T1.1 starts.

## Core requests (CR)
None yet.

## Findings and live checks
None yet.
