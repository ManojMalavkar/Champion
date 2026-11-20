# Track T6 - Full vehicle model (crash model handling)

Branch `track/vehicle-model`, library `champion/lsdyna/`, agent package `champion/specialists/model/`, tests `tests/vehicle_model/`. Status: can start now (M0 done).

## Goal
Pure Python library for LS-DYNA models of 1-8 GB, reused by PyDyna (T1.5) and PRIMER (T4.4), and a `model_agent` for whole-model questions. Ideas from crash-simulation-agent (keyword scanner, entity graph, numbering ranges), read only there.

## Sub-steps
- [ ] T6.1 Streaming keyword index for files of 100 MiB or more, cached beside the model (management keywords only, never the ~20M nodes and elements); include-file tree.
- [ ] T6.2 `model_agent` with `model_outline`, `model_find`, `model_read_range`; hard limit `pydyna.max_model_bytes`.
- [ ] T6.3 Numbering ranges per include file; PART -> SECTION -> MAT graph.
- [ ] T6.4 Compare two iterations (includes, parts, materials changed).
- [ ] T6.5 Keyword-aware edits on the streaming patch engine of `file_edit`, with diff, approval and undo.
- Verify (track): outline and find on a model over 1 GB; the second question uses the cached index.

## Decisions
* Index only management keywords; a model's numbering range per include file is its stable identity across iterations (user's convention).

## Core requests (CR)
None yet.

## Findings and live checks
None yet.
