# PyDyna T1.1 follow-up: CR-4 stores (26 Nov 2026)

Main `88d2fe1` (CR-4) replaces Chroma with Champion's own store (`.npy` + SQLite, folders `index` / `index_<name>`) and refuses the old `vector_db*` names. This step moves the PyDyna track onto it.

## What changed

| File | Change |
| --- | --- |
| merge of `origin/main` `88d2fe1` | no conflicts (no track file touched by CR-4) |
| `champion/specialists/pydyna/records.py` | new `keyword_files` (schema + curated reference) and `dpf_post_files`; `dpf_post_documents` uses `dpf_post_files` |
| `champion/specialists/pydyna/tools.py` | `STORE = "index"`, `DPF_CORE_STORE = "index_dpf_core"`; all four collections get `documents` and `files` (built from the JSONL); `database_path`, `dpf_core_database_path` and `check_search_paths` use the new folders |
| `knowledge/pydyna/.gitignore` | ignores `index/`, `index_dpf_core/` (and still the old `vector_db*/`) |
| `tests/pydyna/test_pydyna_tools.py` | new folder names; fake manifest for the fake-store tests (M1 now reads `MANIFEST.json` before the store); the Chroma test replaced by a real build of `lsdyna_keywords` from the fixture JSONL with a fake embedding, category search, backup folder unchanged |
| `tests/pydyna/test_pydyna_source_data.py` | `pydpf_core` builds into `index_dpf_core` without chromadb; store files in the manifest; `index` and the `vector_db_dpf_core` backup not touched |

Not changed: the legacy config keys (`database_path`, `dpf_core_database_path`, `keyword_reference_path`) stay; a search still refuses them when they do not match the root's layout, so an old value pointing at `vector_db` gives "clear the old path keys".

## Layout after the Linux build

```
knowledge/pydyna/
  knowledge-base/*.jsonl      # input (not in Git)
  index/                      # lsdyna_keywords, pydyna_api, pydpf_post (+ MANIFEST.json)
  index_dpf_core/             # pydpf_core (+ MANIFEST.json)
  vector_db/                  # old Chroma, backup, never opened
  vector_db_dpf_core/         # old Chroma, backup, never opened
```

## Tests (serverless, merged worktree, chromadb not installed)

* `tests/pydyna`: 121 passed, 28 skipped. Skips: ansys-dyna-core not installed (15), ansys-dpf-post not installed (8), real knowledge-base JSONL not present (5).
* Full suite: 599 passed, 28 skipped (same skip count as `tests/pydyna`; reasons not listed for the full run).
* Before the fixes, the merge alone failed at import: `pydpf_core: database must be index or index_<name>`.

## Linux checks (after the commit, in the Champion-pydyna worktree)

Run Champion as `python -c "import sys; from champion.cli import main; sys.exit(main())" ...` (shown as `champion` below).

1. Backup checksums before: `find knowledge/pydyna/vector_db knowledge/pydyna/vector_db_dpf_core -type f -exec sha256sum {} + | sort -k2 > /tmp/backup_before.txt`
2. `python -m pytest -q` (expected: all passed; the ansys and data skips gone).
3. `champion kb build --agent pydyna` (all four collections from the JSONL; `pydyna_api` is about 62k records).
4. `champion kb status --agent pydyna --verify`: four collections OK; `vector_db`, `vector_db_dpf_core` shown as old Chroma databases (backup, not used).
5. Compare counts with the backups: `lsdyna_keywords` 3686, `pydyna_api` 62328, `pydpf_post` 1719, `pydpf_core` 13014 (numbers of the old databases). A difference is reported, not fixed silently: it means the JSONL and the old database differ.
6. Four searches through the PyDyna tools (keyword with `category="material"`, PyDyna API, PyDPF-Post, PyDPF-Core): hits with source and distance, no `tests/` paths.
7. Backup checksums after: `... > /tmp/backup_after.txt && diff /tmp/backup_before.txt /tmp/backup_after.txt` (expected: no output).

## Linux results (27 Nov 2026, `f2b87c9`)

| Check | Result |
| --- | --- |
| 2. pytest | 627 passed, 0 skipped |
| 3. build | all four built, 384, l2 |
| 4. status --verify | all OK, source files match; `vector_db`, `vector_db_dpf_core` shown as old Chroma databases (backup, not used) |
| 5. counts | `lsdyna_keywords` 3686 and `pydpf_core` 13014 equal; `pydyna_api` 36937 (old 62328) and `pydpf_post` 1352 (old 1719) |
| 6. searches | four searches with source and distance; no `tests/` or `codegen/` paths; keyword hits with `category="material"` all `*MAT_*` |
| 7. backups | 53 files, checksums unchanged |

The two smaller counts are explained: the old Chroma databases held every JSONL record and the search dropped test, codegen and repository hits afterwards; the new build applies the same `records.py` filters (`keep_api_path`, `keep_code_path`) before storing. Checked on the JSONL: `pydyna_api` 62328 = 36937 kept + 25391 dropped (codegen 15467, tests 9735, .github 48, agents 47, doc 41, benchmarks 28, ...); `pydpf_post` 1719 = 1352 + 367 (tests 302, .github 36, examples 7, doc 4, ...). `pydpf_core` was already filtered by the old builder. So the search sees the same records as before (same filter), only the store holds fewer; old and new hits were not compared one by one.

Finding: `search_pydpf_core` can return generated protobuf modules (`src/ansys/grpc/dpf/*_pb2.py`), which `keep_code_path` keeps. Not changed here; dropping them is a separate step (changes the `pydpf_core` count, needs a rebuild with `--replace`).

## Status

Built and committed 26 Nov 2026 (user OK), pushed to `track/pydyna` together with the merge of main `88d2fe1` (author ManojMalavkar). Linux checks done 27 Nov 2026, all passed.
