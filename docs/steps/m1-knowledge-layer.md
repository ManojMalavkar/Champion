# M1 - Shared knowledge layer

Built and pushed 24 Nov 2026 on `main` (`6e369af`, "Step M1"). 377 tests pass on Databricks serverless (349 before, 28 new in `tests/test_knowledge.py`).

**Changed by CR-4 (26 Nov 2026, `docs/steps/cr-04-own-vector-store.md`):** decision 1 did not hold with chromadb 1.5.9 (it writes into its database on every open). The store is now Champion's own files (`<collection>.npy` + `<collection>.sqlite3` in `index/` or `index_<name>/`), read only, exact search, no Chroma. The layout, `store.py`, `builder.py`, `manifest.py` (format 2), the folder names and `--allow-foreign` below are updated; the Evidence section is the M1 history.

## Why

PyDyna, ANSA/META and PRIMER each search Chroma databases. Without M1 every track would write its own Chroma code, its own model loading and its own path checks. M1 does this once in `champion/knowledge/`; a track only declares its collections and gets tools.

## Decisions (user, 24 Nov 2026)

1. Chroma databases are opened in place, read-only; if any file changes while open, the store stops. (Replaced by CR-4: Champion's own store files, opened read only; the stop on change stays.)
2. Builder: a library function plus `champion kb build --agent X`.
3. Option 3b: the file limits used by core tools move from `pydyna` to a new `files` section; the rest of `pydyna` leaves core and is declared by the PyDyna package (`config_section`).
4. `champion kb status` is part of M1.

## Layout

```
<project>/knowledge/<agent>/          # <section>.knowledge_root, default knowledge/<agent>
  knowledge-base/*.jsonl              # sources (committed)
  index/                              # store (built locally, in .gitignore), CR-4
    <collection>.npy                  # float32 vectors (count, dimension)
    <collection>.sqlite3              # records (row, id, document, metadata) and build info
    MANIFEST.json                     # written by champion kb build
  index_<name>/                       # more stores (for example index_dpf_core)
  vector_db*/                         # old Chroma databases: backups, never opened
```

The root must be inside the project folder and is not a symbolic link; store folder names are `index` or `index_<name>`.

## Modules

| Module | Does |
| --- | --- |
| `root.py` | `resolve_root(config, agent, section)`: checked root, `kb_dir`, `db_dir(name)` |
| `manifest.py` | read / write `MANIFEST.json` format 2 (count, model, dimension, space, source files and store files with SHA-256); reads format 1 (T1.3b, first M1 builder) too |
| `embedding.py` | sentence-transformers from the local cache only (`knowledge.offline`); clear error with the download command; `set_embedder` for tests |
| `store.py` | `ReadOnlyStore`: `.npy` loaded with `mmap_mode="r"`, `allow_pickle=False`; SQLite `mode=ro&immutable=1`; files checked against the manifest and each other; exact search (l2 squared, cosine, ip as Chroma); size and time of every file checked after every use; a change raises `DatabaseChanged` and the store stays stopped |
| `registry.py` | `KnowledgeSpec` / `KnowledgeCollection` / `Document`; `register_knowledge` |
| `search.py` | `search` (vector, filters, track `keep` function, fetch factor), `lookup` (ids or exact metadata), `format_hits` (score, distance, source, chunk) |
| `tools.py` | `make_search_tool`, `make_lookup_tool`: FunctionTools with right `none`, strict schema, `run_guarded` (audit, output cap) |
| `builder.py` | `build_collection`: records -> embeddings -> temporary files -> checked -> renamed into place -> manifest entry |
| `status.py` | checks for `champion kb status` and `champion doctor` |

## How a track uses it

```python
from champion.knowledge.registry import KnowledgeCollection, KnowledgeSpec, register_knowledge
from champion.knowledge.tools import make_search_tool

MODEL = "BAAI/bge-small-en-v1.5"
register_knowledge(KnowledgeSpec("pydyna", (
    KnowledgeCollection("lsdyna_keywords", MODEL, documents=keyword_records, files=keyword_files),
    KnowledgeCollection("pydpf_core", MODEL, database="index_dpf_core", documents=..., files=...),
), config_section="pydyna"))

search_keywords = make_search_tool(name="search_lsdyna_keywords", agent="pydyna",
                                   collection="lsdyna_keywords",
                                   description="Search the LS-DYNA keyword manual.")
```

A collection without `documents` can be searched (once built) but not built by `champion kb build`.

## Configuration

* `files.large_file_bytes`, `files.max_model_bytes`, `files.model_timeout_seconds` (were `pydyna.*`; the old names still work and give a "please rename" warning; if both are set, `files.*` wins).
* `knowledge.offline` (true), `knowledge.verify_hash` (true), `knowledge.max_results` (20, at most 100), `knowledge.fetch_factor` (5), `knowledge.max_hit_chars` (1500).
* `<section>.knowledge_root` in each agent's own section.
* `pip install -e '.[knowledge]'` installs numpy and sentence-transformers (`.[pydyna]` still works); chromadb is not needed (CR-4).

## Commands

* `champion kb status [--agent X] [--verify]`: root, store folders, manifest, count per collection, model in the cache, old Chroma folders as backups; `--verify` compares source files with the manifest. Exit 1 on FAIL.
* `champion kb build --agent X [--collection C] [--replace]`: refuses an existing collection without `--replace`, duplicate ids, bad metadata or vectors; a failed build leaves the previous files. (`--allow-foreign` removed by CR-4.)
* `champion doctor`: the same knowledge lines.

## Evidence

* Chroma 1.5.9 probe: opening, query and get change no file (stat and SHA-256).
* Tests: search and lookup leave every database file with the same SHA-256; a file added while open stops the store; model or dimension different from the manifest is refused; status FAILs on a changed source file or a wrong count.
* Real model (bge-small-en-v1.5): not cached -> clear offline error; cached -> vectors equal Chroma's `SentenceTransformerEmbeddingFunction` (384 numbers, max difference 0.0).
* A database built the old agent's way (Chroma embedding function, no manifest) gives through M1 the same ids and distances as a direct Chroma query, and `kb status` shows WARN (no manifest) and OK (4 entries).

## Open

* PyDyna track: replace the `search.py` stub with `KnowledgeSpec` and the tool factory; declare its `pydyna` section (`database_path`, `keyword_reference_path`, `python_path`, include and memory limits) after merging `main`.
* Local check on the Linux machine: `pytest -q`, `champion kb status --agent pydyna` on the real databases.
