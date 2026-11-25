# Step T1.3b - DPF-Core sub-agent and its knowledge data

Track T1 (PyDyna), branch `track/pydyna`. Built 24 Nov 2026, committed together with T1.3a (user OK). The generated JSONL is not in Git (user).

## What the user asked (24 Nov 2026)
* A separate fourth sub-agent DPF-Core next to DPF-Post.
* Its search data generated from https://github.com/ansys/pydpf-core.git, not uploaded to any server: the JSONL is downloaded from the Databricks workspace, or a script generates it on the local machine.
* The same script for PyDyna (https://github.com/ansys/pydyna.git) and PyDPF-Post (https://github.com/ansys/pydpf-post.git).
* Its own database folder: `knowledge/pydyna/vector_db_dpf_core`.

## What was built
| File | What |
| --- | --- |
| `champion/specialists/pydyna/source_data.py` | Generator: `git clone --depth 1` of a tag (default `v<installed version>`), or `--source` checkout; keeps `.py`, `.rst`, `.md` under `src/`, `doc/`, `docs/`, `examples/` (same rule as the search); chunks of at most 1800 characters cut at `class` / `def` (Python) and headings (Markdown outside code blocks, reStructuredText); records in the old format (`class`, `method` = `<path>#chunk-<n>`, `description`, `source_project`, `source_path`, `chunk_index`) plus `start_line`, `end_line`, `version`, `commit`; over 40 MB split into `-part-0001`...; never overwrites without `--force`; `MANIFEST-<project>.json` (repository, tag, commit, counts, SHA-256 per file). Standard library and `git` only. |
| `champion/specialists/pydyna/build_index.py` | Removed in T1.1 (25 Nov 2026): M1 builds with `champion kb build --agent pydyna --collection pydpf_core`, same model, `l2`, same `MANIFEST.json` format. Was: index builder (stub until M1): one Chroma collection from the JSONL with the search's embedding model (`BAAI/bge-small-en-v1.5`); refuses an existing collection without `--replace` and the main `vector_db` folder without `--allow-main-db`; checks the count; `MANIFEST.json` in the database folder. |
| `records.py` | Collection `pydpf_core` (`pydpf_core_source.jsonl` or parts, ids `dpfcore:<path>#<chunk>`, metadata with `version`); PyDPF-Post may now also come in parts. |
| `tools.py` | Tool `search_pydpf_core` (right `none`), database `pydyna.dpf_core_database_path`, default `vector_db_dpf_core` next to `pydyna.database_path`. |
| `__init__.py` | Sub-agent `pydyna_dpf_core` (DPF-Core): `search_pydpf_core`, `lookup_dpf_api`, `find_dpf_operators`, `check_dpf_script`. DPF-Post keeps its four tools and now covers the high-level PyDPF-Post API; the group sends operators, workflows and binout work to DPF-Core. |
| `tests/pydyna/test_pydyna_source_data.py` | 12 tests: chunking, file rules, record format, manifest and SHA-256, parts and `--force`, file names read by `records.py` for all three projects, clone of a local Git tag, command line, builder with a fake client, main-database guard, the DPF-Core search. |

## Data generated on Databricks (for download)
Folder `/Workspace/Users/t0018k6@inetpsa.com/pydyna-knowledge-download/` (outside the Git folder):
* `pydpf_core_source.jsonl`: 13,014 records, 23,600,438 bytes, SHA-256 `10f65edcecaecf09d853667c08195e004509e02d21f7ce3971b39e3a8bac3748`
* `MANIFEST-pydpf-core.json`: tag v0.16.3, commit `b36dd386bf7f2c616d6b8877f36a49c067e68442`, 1,327 of 1,539 files

Checked against a separate clone: all 1,327 eligible files present, no text lost, unique ids, chunks of at most 1800 characters.

## Linux steps
In the Champion worktree with the venv active (`source ~/.ansa_meta_copilot/.venv/bin/activate`).

1. Tests (after pulling): `python -m pytest -q tests/pydyna` (expect 145 passed, 0 skipped), `python -m pytest -q` (expect 494 passed).

2. Data: either copy the downloaded file, or generate it here (same result for the same tag):

   ```bash
   # a) downloaded
   cp ~/Downloads/pydpf_core_source.jsonl ~/Downloads/MANIFEST-pydpf-core.json knowledge/pydyna/knowledge-base/
   sha256sum knowledge/pydyna/knowledge-base/pydpf_core_source.jsonl   # 10f65edc...3748
   # b) or generated (needs git and access to github.com)
   python -m champion.specialists.pydyna.source_data pydpf-core --out knowledge/pydyna/knowledge-base
   ```

   PyDyna and PyDPF-Post work the same way (`pydyna`, `pydpf-post`, or `all`). Write them to another folder first (for example `--out /tmp/new-kb`): the old files in the knowledge base stay in use until you decide to replace them.

3. Distance of the old collections, read only (so the new one can use the same):

   ```bash
   python - <<'EOF'
   import sqlite3
   db = sqlite3.connect("file:knowledge/pydyna/vector_db/chroma.sqlite3?mode=ro", uri=True)
   for row in db.execute("SELECT c.name, m.key, m.str_value FROM collections c LEFT JOIN collection_metadata m ON m.collection_id = c.id"):
       print(row)
   for row in db.execute("SELECT name, config_json_str FROM collections"):
       print(row[0], row[1][:300])
   EOF
   ```

   On Linux (24 Nov 2026) all three collections have no `hnsw:space` and config `{}`: Chroma's default `l2`. `build_index.py` uses `l2` by default since then; give `--space cosine` only if a database shows `cosine`.

4. Build the collection into its own folder (CPU, a few minutes for 13,014 chunks). Done on Linux 24 Nov 2026; since T1.1 (25 Nov 2026) steps 4 and 5 are done with M1 instead: build `HF_HUB_OFFLINE=1 champion kb build --agent pydyna --collection pydpf_core` (add `--replace` only to build it again; do not rebuild the existing database), check `champion kb status --agent pydyna`, search through the tool `search_pydpf_core`. The commands below were for the removed `build_index.py` / `search.py`:

   ```bash
   HF_HUB_OFFLINE=1 python -m champion.specialists.pydyna.build_index --collection pydpf_core \
       --kb knowledge/pydyna/knowledge-base --db knowledge/pydyna/vector_db_dpf_core
   ```

   Expected last line: `pydpf_core: 13014 entries in knowledge/pydyna/vector_db_dpf_core`.

5. Search check:

   ```bash
   HF_HUB_OFFLINE=1 python - <<'EOF'
   from pathlib import Path
   from champion.specialists.pydyna.search import backend_for
   backend = backend_for(Path("knowledge/pydyna/vector_db_dpf_core"))
   for query in ("part internal energy from LS-DYNA binout", "contact force of an interface d3plot",
                 "create a workflow and connect operators"):
       print(query)
       for hit in backend.query("pydpf_core", query, 5):
           print(f"  {hit.distance:.4f} {hit.metadata['source_path']}#{hit.metadata['chunk_index']}")
   EOF
   ```

   Expected: the first query finds `operators/result/part_internal_energy.py` or an LS-DYNA example, the third workflow docs or examples. Paste the output back.

`knowledge/pydyna/.gitignore` keeps both databases and the knowledge JSONL out of Git (in a worktree `.git` is a file, so `.git/info/exclude` cannot be used there). Check: `git status --short knowledge/` prints nothing.

## Not done here
* The old `vector_db` is not written; replacing the old PyDyna and PyDPF-Post files with regenerated ones is the user's decision.
* M1 replaced `search.py` and `build_index.py` in T1.1 (docs/steps/pydyna-07-m1-integration.md).
