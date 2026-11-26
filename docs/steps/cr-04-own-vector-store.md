# CR-4 - M1 knowledge store in Champion's own files (no Chroma)

Status (26 Nov 2026): built, committed and pushed on `main` (shared M1 code, commit "CR-4"); asked by T1 PyDyna. Design agreed by the user on 25 Nov 2026 ("Build CR-4 as described"). 478 tests pass on Databricks serverless without chromadb installed, none skipped (`main` before: 456, of which 9 Chroma tests skip without chromadb). Track follow-up and Linux checks below are still open.

## Why

M1 decision 1 (24 Nov 2026) was: Chroma databases opened in place, read-only; the store stops if a file changes. With chromadb 1.5.9 this cannot hold:

* On the user's Linux machine `kb status --agent pydyna` changed both `chroma.sqlite3` files (`sha256sum -c` failed); the store noticed only after Chroma had written.
* Tested on serverless (chromadb 1.5.9): `PersistentClient` writes `chroma.sqlite3` and `length.bin` on every open, also for a database it built itself; with read-only files Chroma refuses to open.

Reading Chroma's internal files directly was checked and works, but was rejected by the user (25 Nov 2026) because it depends on Chroma's internal layout.

## Decisions (user, 25 Nov 2026)

1. Champion's own files: vectors in a NumPy `.npy`, documents and metadata in SQLite, opened read-only; exact search with NumPy; no Chroma at all.
2. The existing Chroma databases (`knowledge/pydyna/vector_db`, `vector_db_dpf_core`) are kept untouched as a backup; the new build goes into new folders.
3. All collections are built again from the knowledge-base JSONL with the same model.

## Layout

```
<project>/knowledge/<agent>/
  knowledge-base/*.jsonl           # sources (unchanged)
  index/                           # new: default store folder (KnowledgeCollection.database = "index")
    <collection>.npy               # float32, shape (count, dimension), row i = record row i
    <collection>.sqlite3           # records and build info
    MANIFEST.json                  # count, model, dimension, space, sources and store files with SHA-256
  index_<name>/                    # more store folders (for example index_dpf_core)
  vector_db/ ...                   # old Chroma databases: never opened, never written (backup)
```

Folder names: `index` or `index_<name>` (lowercase letters, digits, `_`). `vector_db*` folders are no longer a valid store name, so nothing can build into or search the backups.

`<collection>.sqlite3`:

| Table | Columns |
| --- | --- |
| `records` | `row INTEGER PRIMARY KEY` (0 .. count-1, the vector row), `id TEXT NOT NULL UNIQUE`, `document TEXT NOT NULL`, `metadata TEXT NOT NULL` (JSON object) |
| `info` | `key TEXT PRIMARY KEY`, `value TEXT` (format version, model, dimension, space, count) |

## Modules

| Module | Change |
| --- | --- |
| `root.py` | store folder names `index` / `index_<name>` (`DEFAULT_DATABASE = "index"`); `databases()` lists them; `old_databases()` lists old `vector_db*` folders separately as backups |
| `store.py` | `ReadOnlyStore` with the same interface (`collection_names`, `count`, `space`, `query`, `get`, `check_unchanged`, `DatabaseChanged`). Opens `<collection>.sqlite3` with `mode=ro&immutable=1` and `<collection>.npy` with `np.load(mmap_mode="r", allow_pickle=False)`; checks dtype float32, 2-D, rows = `count` in `info` and in `records`, dimension = manifest; with `knowledge.verify_hash: true` also the SHA-256 of both files against the manifest. Fingerprint of the folder before and after loading and after every query stays. |
| search | exact distance over the collection (or the rows the filters leave): `l2` = squared L2 like Chroma, so `score = 1 - distance` and the shown distances stay comparable with the old results; `cosine` and `ip` also supported. Filters: equality on metadata keys, done in SQLite (`json_extract`), then NumPy over those rows. |
| `builder.py` | no Chroma: records -> embeddings (batches of 256, progress) -> files written under temporary names in the same folder, checked (count, dimension, ids unique, finite values), then renamed into place; manifest entry written last. An existing collection is replaced only with `--replace`; a failed build leaves the previous files as they were. |
| `manifest.py` | entry gains `store_files` (name, size, SHA-256 of the `.npy` and `.sqlite3`); format version 2; version 1 entries (Chroma builds) are read only to show them as old. |
| `status.py` | per store folder: OK / FAIL per collection (files present, count, model, dimension, optional `--verify` hashes), WARN for a collection not built yet; old `vector_db*` folders shown as `OK ... old Chroma database (backup, not used)` (not `INFO`: `champion doctor` knows only OK, WARN and FAIL). |
| `pyproject.toml` | extras `knowledge` and `pydyna`: `sentence-transformers` and `numpy`; `chromadb` removed. |
| others | `registry.py` (database must be `index` / `index_<name>`), `search.py` (a store folder without manifest or a collection not built is refused with the build command), `cli.py` (`--allow-foreign` removed), `doctor.py` (optional package `numpy` instead of `chromadb`), `config.py` (comment of `knowledge.verify_hash`), `.gitignore` (`knowledge/*/index/`, `knowledge/*/index_*/`). |

`champion kb build --agent X [--collection Y] [--replace]` and `champion kb status` keep their options; `--allow-foreign` is removed (a store folder can no longer hold a foreign database).

## Tests (`tests/test_knowledge.py`, updated)

With a fake embedding model; no Chroma installed or needed:

1. Build writes both files and the manifest; count, dimension, ids, documents and metadata read back equal to the records.
2. Search equals a brute-force reference (l2, cosine, ip), with and without filters; `score` and `distance` as before.
3. SHA-256 of every file in the folder before and after open, search and lookup: unchanged; the store opens nothing for writing (checked by making the folder read-only).
4. Refused with a clear error: object or float64 `.npy`, wrong shape, rows not equal to `records`, missing or changed manifest entry (`--verify`), duplicate ids, a `vector_db*` folder name, a symbolic link.
5. Build without `--replace` on an existing collection is refused; a build that fails half-way leaves the previous files unchanged.
6. `champion kb status` lines for a good store, a broken store and an old Chroma folder.
7. `chromadb` is not imported by build, status or search (checked with `sys.modules["chromadb"] = None`, so any import fails, and no knowledge module contains `import chromadb`).
8. Full suite.

## Evidence (26 Nov 2026, Databricks serverless, numpy 2.3.4, SQLite 3.45.1, chromadb not installed)

* `tests/test_knowledge.py`: 47 tests (all of the groups above, plus a filter test with an SQLite without JSON functions, a pickled `.npy` that would create a file when loaded: refused and nothing created, a store folder through a link, bad vectors from the model, build replacing an open store). Full suite **478 passed**, none skipped (`main` `5ba653e` on the same machine: 447 passed, 9 skipped).
* Each test group checked that it can fail: with the store changed on purpose (wrong cosine, wrong ip, l2 not squared, filters ignored, SQLite opened for writing, SHA-256 check left out) at least one test fails each time; with the real code all pass.
* Read only: the folder and files made read-only (not root, uid 1004): search, lookup and `kb status --verify` work; SHA-256 of every file unchanged; no journal or lock file; the store's SQLite connection refuses `create`, `delete` and `update` even when the files are writable.
* Size check with random data of the largest PyDyna collection (62,328 x 384): open with SHA-256 0.18 s, one search 21 ms, one search with a filter 109 ms.

## Track follow-up (T1 PyDyna, after the merge of CR-4)

* `tools.py`: all four collections get a `documents` function from `records.py` (today only `pydpf_core` has one), `database="index"` for the first three, `"index_dpf_core"` for `pydpf_core`; the old path keys (`database_path`, `dpf_core_database_path`) point to `index` folders or are dropped.
* `knowledge/pydyna/.gitignore` also ignores `index*/`.

## Linux checks (after CR-4 and the track follow-up are merged)

In the `Champion-pydyna` worktree (the venv's `champion` points to the main folder, so use `python -c "import sys; from champion.cli import main; sys.exit(main())" ...`):

1. Checksums of every file in `vector_db` and `vector_db_dpf_core` (the backups) before.
2. `python -m pytest -q`.
3. `kb build --agent pydyna` (all four collections; time and memory noted).
4. `kb status --agent pydyna --verify`: four collections OK, backups shown as old.
5. New ids and documents compared with the backup (old `chroma.sqlite3` read with SQLite `mode=ro&immutable=1`, which was shown not to change it); differences listed.
6. The four searches through the PyDyna tools.
7. Backup checksums after: unchanged.

## Not in this change

* No approximate index (HNSW); exact search is fast enough for these sizes (largest 62,328 x 384). Added only if a larger collection needs it.
* The backups are not deleted; that stays the user's decision.
