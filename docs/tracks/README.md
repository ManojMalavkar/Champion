# Track plans

One file per track: its sub-steps with checkboxes, decisions, findings and live-check lists. PLAN.md keeps only the overview, the core requests (P.6) and one status line per track (P.7).

| Track | File | Branch | Folder for the agent package |
| --- | --- | --- | --- |
| T1 PyDyna | [pydyna.md](pydyna.md) | `track/pydyna` | `champion/specialists/pydyna/` |
| T2 HPC | [hpc.md](hpc.md) | `track/hpc` | `champion/specialists/hpc/` |
| T3 ANSA/META | [ansa-meta.md](ansa-meta.md) | `track/ansa-meta` | `champion/specialists/ansa_meta/` |
| T4 PRIMER | [primer.md](primer.md) | `track/primer` | `champion/specialists/primer/` |
| T5 Skills | [skills.md](skills.md) | `track/skills` | `champion/skills/` |
| T6 Full vehicle model | [vehicle-model.md](vehicle-model.md) | `track/vehicle-model` | `champion/lsdyna/`, `champion/specialists/model/` |
| T8 Memory | [memory.md](memory.md) | `track/memory` | `champion/memory/` |

## Rules (PLAN.md P.2, short)

* Add files only in your own places: the package above, `tests/<track>/` (file names `test_<track>_*.py`), `docs/tracks/<track>.md`, `docs/steps/<track>-NN-*.md`, `config/examples/<section>.yaml`, `knowledge/<agent>/`.
* Never edit shared files (`champion/core/`, `policy/`, `execution/`, `tools/`, `agents/`, `chat.py`, `cli.py`, `doctor.py`, `registry.py`, `config.py`, PLAN.md, REPORT.md). Need a change there: write a core request (CR) in your file and ask for it on `main`.
* Every tool: a tool-policy entry, `check_path` for paths, edits through the change list, `approval.ask` for risky actions, audit through `run_guarded`.
* Each step: tests (track + all of `main`) pass, walkthrough, one commit, the user's live check, then the next step. Merge `main` into the track after every core merge.

## Template for a track file

```markdown
# Track Tn - Name
Branch `track/<name>`, package `champion/specialists/<name>/`. Status: ...
## Goal
## Sub-steps
- [ ] Tn.1 ... Verify: ...
## Decisions
## Core requests (CR)
## Findings and live checks
```

## Linux machine

One worktree per track, next to the main clone:

```bash
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion
git fetch
git worktree add ../Champion-hpc track/hpc     # then work in ../Champion-hpc
```
