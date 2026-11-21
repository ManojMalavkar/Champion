# Track T1 - PyDyna

Branch `track/pydyna`, package `champion/specialists/pydyna/`, tests `tests/pydyna/`, knowledge `knowledge/pydyna/`. Status: source reviewed 21 Nov 2026 (local PyDyna_Agent report below); data copied into the Linux worktree; T1.0 built 21 Nov 2026; the 5 tools built 21 Nov 2026 with a track-local search stub until M1 (`search.py`); CR-1 done and merged (full suite here with the group on: 310 passed, 5 skipped); T1.0 and the tools committed together 21 Nov 2026 (user OK); local checks on Linux pending.

## Goal
One group `pydyna_agent` with 3 sub-agents (user, 20 Nov 2026): Keyword Lookup, PyDyna Code, PyDPF-Post. Tools ported from PyDyna_Agent (read only there).

## Working flow (user, 21 Nov 2026)
Code, tests and plans are written in Databricks (`Champion-pydyna` Git folder). The knowledge data lives only on the Linux machine; checks that need it are run there by the user's local Codex agent (read only) and the output is pasted back. Tests that need the real data skip when it is missing and are listed under "Pending local checks".

## Source of the port (read only)
The working agent is the local copy on the Linux machine: `/nobackup/people/t0018k6/script/Github_repo/LLM/PyDyna_Agent` (same commit as GitHub `05f93c9`, nothing to commit). Its data:

| Collection | Count | Built from (by `sacreate.py`) | Size |
| --- | --- | --- | --- |
| `lsdyna_keywords` | 3686 | `keyword_schema.jsonl` (3685, from PyDyna `codegen/kwd.json`) + `keyword_reference.jsonl` (5 curated; 4 replace generated, 1 new) | 24.6 MB |
| `pydyna_api` | 62328 | `pydyna_docs.jsonl` (5) + `pydyna_source-part-0001..0004.jsonl` (62323) | 127 MB |
| `pydpf_post` | 1719 | `pydpf_post_source.jsonl` (PyDPF-Post source chunks) | 3.4 MB |

* Embedding `BAAI/bge-small-en-v1.5` on CPU, vector length 384 (checked in the stored database); model is in the local Hugging Face cache, loading without internet not yet verified.
* Builder that matches the data: `sacreate.py` (`--keywords`, `--vec`, `--post`). `bin/build_vector_db.py` is outdated (indexes only the 5 docs into `pydyna_api`); do not port it.
* `keyword_schema.jsonl` and `pydpf_post_source.jsonl` exist only locally (ignored by `*.jsonl` in that repo). The PyDyna and PyDPF-Post source checkouts used to generate them are gone, so these files cannot be regenerated now; they are copied as they are.
* Local versions: Python 3.11.2, chromadb 1.5.9, sentence-transformers 5.6.1, ansys-dyna-core 0.12.1, ansys-dpf-core 0.16.3; ansys-dpf-post not installed.
* Not ported: 4 LLM backends, Gradio apps, JSONL/SQLite logging, analytics, admin and log agent, `config_secrets.py`, `certs/` (Champion runtime, audit and TUI cover these).

## Problems found in the source (fixed in the port)
1. Curated keyword records replace the whole generated record: `lookup_keyword("*MAT_024")` returns only 5 fields (RO, E, PR, SIGY, ETAN) instead of the full card. Port: merge curated over generated (generated cards and fields kept; curated description, aliases, units, required flags and limits added).
2. Ids are sequential (`kw_0`, `api_0`, `dpfpost_0`) and change when input order changes. Port: stable ids from keyword name or `source_path` + `chunk_index`.
3. `pydpf_post` indexed repository files such as `.pre-commit-config.yaml` and `CODE_OF_CONDUCT.md`. Port: index only `.py`, `.rst`, `.md` under source, docs and examples; drop repository meta files.
4. `validate_keyword_params` does not check field types. Port: check types too.
5. Search returns only a rounded score. Port: return raw distance and score, with source file and chunk.
6. The old prompt asks for a Python example for every how-to, even simple lookups. Port: only the Code and PyDPF-Post sub-agents write code; Keyword Lookup answers with fields and sources.

## Sub-steps
- [x] T1.0 Data and records (no M1 needed; built 21 Nov 2026, walkthrough docs/steps/pydyna-01-records.md, 294 passed + 5 skipped on serverless): JSONL files in `knowledge/pydyna/knowledge-base/` (Linux worktree only, not in Git); pure-Python record layer in `champion/specialists/pydyna/records.py` (load JSONL, merge curated keywords over generated, stable ids, PyDPF-Post file filter, record -> document text and metadata as in `sacreate.py`, field type check); unit tests on small fixtures in `tests/pydyna/fixtures/` run here; `tests/pydyna/test_pydyna_data.py` checks the real files (counts 3685 + 5 -> 3686 keywords, 62328 API records, 1719 PyDPF-Post before the filter, keys per file, curated keywords present in the schema, `*MAT_024` merged has more than 5 fields) and skips when the data is missing. Verify: unit tests pass here; real-data tests pass on Linux (pending local check).
- [x] T1.1a Tools and group on a search stub (built 21 Nov 2026, walkthrough docs/steps/pydyna-02-tools.md, 310 passed + 5 skipped on serverless): group `pydyna` with `pydyna_keyword`, `pydyna_code`, `pydyna_dpf_post`; tools `lookup_keyword`, `validate_keyword_params`, `search_keyword` (category filter), `search_pydyna_api`, `search_pydpf_post`; search through `search.py` until M1. Verify: unit tests here; real searches on Linux (pending local check).
- [ ] T1.1 Keyword Lookup (needs M1): switch the search to M1 and remove `search.py`. Verify: `*MAT_024` returns the full card fields plus the curated aliases and units, with source; a material question with `category="material"` returns only `*MAT_*`.
- [ ] T1.2 PyDyna Code (needs M1): `search_pydyna_api` over docs + source chunks, templates; writes PyDyna (`ansys-dyna-core`) scripts into the working folder, no run. Verify: a deck-building script passes `code_check`.
- [ ] T1.3 PyDPF-Post (needs M1): `search_pydpf_post` on the filtered collection, d3plot/binout scripts. Verify: top hits for "section force from binout" come from source or examples, and a section-force or energy curve script passes `code_check`.
- [ ] T1.4 Run with approval (needs M3): host Python backend in a run folder.
- [ ] T1.5 Model questions (needs T6).
- [ ] T1.6 Skills (needs T5).

## Decisions
* Group of 3 sub-agents; database in `knowledge/pydyna/` (`vector_db/` built locally; JSONL not in Git for now, see below; differs from P.8 item 7, to be updated on `main`).
* The built-in `pydyna` config section still lives in `config.py` `DEFAULTS`; moving it into this package is a core request when T1.1 starts.
* Embedding model `BAAI/bge-small-en-v1.5` (384) for all three collections (verified 21 Nov 2026).
* The M1 builder follows `sacreate.py` (document text and metadata per record), with the fixes above; the PyDyna-specific record conversion stays in this package (T1.0), M1 stays generic.
* No knowledge-base file in Git for now (user, 21 Nov 2026). A private Hugging Face dataset with a SHA-256 `MANIFEST.json` was proposed (huggingface.co reachable from Databricks serverless); upload deferred by the user until the Stellantis policy is clear. Script kept at the end of this file.
* `lookup_keyword` keeps units and limits from the curated record (built as proposed in T1.0; confirm).
* `get_material_model` and `get_contact_type` are not ported; `search_keyword` has a `category` filter instead (user, 21 Nov 2026).
* The tools have right `none`: they read only Champion's knowledge base from the config, so they also work at access levels without read.

## Requirements for M1 from this track (input for the M1 thread on `main`)
* Knowledge root with a `MANIFEST.json` (embedding model, dimension, expected collection counts, file SHA-256); `champion doctor` compares the database against it.
* Embedding model loaded from the local cache without internet (`HF_HUB_OFFLINE`); clear doctor message when missing.
* Open the Chroma database read only or on a copy: Codex noted `PersistentClient` can write on start.
* Search results with raw distance, score, source file and chunk.
* A track supplies its own record -> document function; M1 does the indexing and searching.

## Core requests (CR)

### CR-1 (21 Nov 2026): core tests must not see track agents - done (`main` 255aa86, merged into `track/pydyna` 21 Nov 2026 as a fast-forward; walkthrough `docs/steps/cr-01-core-tests-hide-track-agents.md`)
* **What (on `main`, `tests/conftest.py` only):** an autouse fixture for tests directly in `tests/`: it calls `registry.load_builtin_specialists()`, takes the names found there other than `coder` and `linux_coder` (the track agents), and monkeypatches `registry.registered_specialists` to leave them out. Agents a test registers itself (dummy specs) stay visible; tests in `tests/<track>/` see all agents. No change to Champion itself.
* **Why:** registering the `pydyna` group (on by default: `agents.pydyna.enabled: True` is in `config.py` `DEFAULTS`) makes 11 core tests fail, because they expect only Coder and Linux to be offered:
  * `test_filesystem_tools.py::test_tools_hidden_when_level_gives_no_read`
  * `test_linux_specialist.py::test_linux_spec_and_registration`, `::test_read_requests_at_read_only`
  * `test_parallel_enablers.py::test_specialists_are_found_not_listed`, `::test_a_broken_track_is_reported_and_champion_still_starts`, `::test_a_group_runs_its_sub_agents_shown_and_audited`, `::test_without_groups_nothing_changes`
  * `test_runtime.py::test_tool_call_then_answer`
  * `test_specialists.py::test_linux_and_coder_are_the_builtin_specialists`, `::test_specialist_is_offered_to_the_orchestrator`, `::test_rejected_specialist_is_not_offered_and_the_chat_warns`
  Every later track that registers an agent hits the same problem, so the fix belongs in the core.
* **Tried on a copy of `track/pydyna` (21 Nov 2026, serverless):** without the fixture 11 failed, 297 passed; with it 308 passed, 5 skipped. On a copy of `main` with the fixture: 269 passed, as without it. A first version that hid every non-core agent broke 12 tests that register their own dummy agents, so only agents found in track packages are hidden.
* **Accept when:** on `main` the full suite passes as before; after merging `main` into `track/pydyna` the full suite passes with the `pydyna` group registered and on by default.
* **Waits:** committing T1.0 and the tools (user: push them together). Not chosen: renaming the group or switching it off by default (changes the tool name `pydyna_agent`), or editing core tests on the track (P.2).

### Expected later
For M1: `knowledge/*/vector_db/` in the root `.gitignore` (not ignored now; excluded locally through `.git/info/exclude`); the `pydyna` config section moved out of `config.py` `DEFAULTS`; optional dependency group `pydyna` lists chromadb and sentence-transformers without upper limits (local: 1.5.9 and 5.6.1).

## Pending local checks
* Tools: `docs/steps/pydyna-02-tools.md`, Verify: `tests/pydyna` 44 passed, full suite 315 passed; categories; one search per collection on a copy of the database, with `HF_HUB_OFFLINE=1` (also checks loading the model without internet).
* T1.0: on the Linux worktree the T1.0 tests (`test_pydyna_records.py`, `test_pydyna_data.py`: 30 passed, 0 skipped); report the PyDPF-Post count kept of 1719 (docs/steps/pydyna-01-records.md, Verify).

## Findings and live checks
* 21 Nov 2026, local report by Codex (read only): counts, embedding length, builder and tool behaviour as listed above; `lookup_keyword("*MAT_024")` took 0.26 s including index load; the two semantic searches were not run (read-only rule).
* 21 Nov 2026, data copied on the Linux machine into the worktree `/nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna/knowledge/pydyna/` (knowledge-base 155 MB, vector_db 1.48 GB): SHA-256 identical to the source, collection counts in the copy OK (3686 / 62328 / 1719); `git status --short -uall knowledge/ | grep vector_db` empty. Nothing committed.

## Deferred: Hugging Face upload script (not run)
```bash
#!/usr/bin/env bash
# upload_pydyna_kb.sh - upload knowledge/pydyna/knowledge-base to a private HF dataset
# Usage: ./upload_pydyna_kb.sh <hf_user_or_org>/champion-pydyna-kb
set -euo pipefail
REPO=${1:?"Give the dataset repo id, e.g. ./upload_pydyna_kb.sh myuser/champion-pydyna-kb"}
KB=/nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna/knowledge/pydyna/knowledge-base
source ~/.ansa_meta_copilot/.venv/bin/activate

python - "$REPO" "$KB" <<'EOF'
import sys, json, hashlib, pathlib, tempfile, shutil
from huggingface_hub import HfApi
repo, kb = sys.argv[1], pathlib.Path(sys.argv[2])
api = HfApi()
print("Logged in as:", api.whoami()["name"])

files = sorted(kb.glob("*.jsonl"))
assert len(files) == 8, f"expected 8 JSONL files, found {len(files)}"
manifest = {"embedding_model": "BAAI/bge-small-en-v1.5", "embedding_dim": 384,
            "expected_collections": {"lsdyna_keywords": 3686, "pydyna_api": 62328, "pydpf_post": 1719},
            "sources": "Built from Ansys PyDyna (codegen kwd.json, source) and PyDPF-Post source, MIT licence",
            "files": {}}
for f in files:
    h = hashlib.sha256(f.read_bytes()).hexdigest()
    n = sum(1 for _ in f.open(encoding="utf-8"))
    manifest["files"][f.name] = {"sha256": h, "lines": n, "bytes": f.stat().st_size}
    print(f"{f.name:32} {n:6} lines  {h[:12]}")

stage = pathlib.Path(tempfile.mkdtemp())
try:
    (stage / "knowledge-base").mkdir()
    for f in files: shutil.copy2(f, stage / "knowledge-base" / f.name)
    (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    (stage / "README.md").write_text("# Champion PyDyna knowledge base\nPrivate. JSONL sources for Champion track T1. See MANIFEST.json.\n")
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    info = api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(stage),
                             commit_message="PyDyna knowledge base (8 JSONL + manifest)")
    print("Uploaded. Commit:", info.oid)
    print("Private:", api.repo_info(repo, repo_type="dataset").private)
finally:
    shutil.rmtree(stage)
EOF
```