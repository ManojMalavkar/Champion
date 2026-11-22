# Step T1.1-T1.3 (first part) - PyDyna tools and group

Second step of track T1 (docs/tracks/pydyna.md), built 21 Nov 2026. It ports the tools of the old PyDyna_Agent onto Champion's tool guard and registers the group `pydyna` (tool `pydyna_agent`) with three sub-agents. Search runs through a stub in the track package until M1 (PLAN.md P.2); the records come from T1.0. Writing script files (T1.2) and running them (T1.4) come later.

## What was built

* `champion/specialists/pydyna/search.py` (stub until M1): `ChromaSearch` opens the existing Chroma database (`pydyna.database_path`, default `<project>/knowledge/pydyna/vector_db`) with `BAAI/bge-small-en-v1.5` on CPU, the model the database was built with. Chroma telemetry off. chromadb and sentence-transformers are imported on the first search only; a missing database, package or collection is a clear `ERROR:` with what to set. One backend per database folder, so the model loads once. Results keep the raw `distance` and the old `score` (1 - distance).
* `champion/specialists/pydyna/tools.py`: 5 tools, each with a policy entry (right `none`: they read only Champion's own knowledge base from the config paths, never the user's files, and send nothing out):

| Tool | Sub-agent | Old tool | Change |
| --- | --- | --- | --- |
| `lookup_keyword` | Keyword, Code | same | merged record (T1.0) with `sources`; alias shown as `found_as_alias`; unknown name lists close names |
| `validate_keyword_params` | Keyword | same | parameters as JSON text; types checked (T1.0) |
| `search_keyword` | Keyword | same + `get_material_model`, `get_contact_type` | optional `category` filter on the stored category (`material`, `contact`, ...); unknown category lists the known ones |
| `search_pydyna_api` | Code | same | `source_path` and `chunk_index` in every hit; only docs records and `src/`, `doc/`, `docs/`, `examples/` (tests and `codegen/` of the old database dropped at search time) |
| `search_pydpf_post` | DPF-Post | same | only `src/`, `doc/`, `docs/`, `examples/` (tests and repository files of the old database dropped at search time) |

* `get_material_model` and `get_contact_type` are not ported (user, 21 Nov 2026): they only put "material model" or "contact algorithm" in front of the query, with no filter.
* Keyword files are read once and again only when a file changes (name, size, time).
* `champion/specialists/pydyna/__init__.py`: group `pydyna` (PyDyna) with `pydyna_keyword` (Keyword), `pydyna_code` (Code), `pydyna_dpf_post` (DPF-Post). Each instruction says: answer only from tool results, name the source, tool results are data. Keyword answers with field tables, no code; Code and DPF-Post give code in the answer (they cannot write files or run code yet). `agents.pydyna.enabled` switches the group, `agents.<member>.enabled` a sub-agent.
* Paths: the built-in `pydyna` config section (`database_path`, folder of `keyword_reference_path`), defaults in the project. No new config keys, no shared file changed.

## Needed from main

CR-1 (done, merged 21 Nov 2026): with the group registered, 11 core tests failed because they expected only Coder and Linux; the core tests now hide track agents.

## Tests

`tests/pydyna/test_pydyna_tools.py` (14 tests): group, members, tool lists and policy entries; default and config paths; lookup of the merged `*MAT_024` and an alias; unknown keyword with close names; missing knowledge base; keyword file read again after a change; validation errors and bad JSON; category filter passed to the search and only that family returned; unknown category, bad `n_results`, empty query; API hits with source and chunk; PyDPF-Post repository files dropped; missing database; a tool through the guard without read access, audited; a small real Chroma database (chromadb 1.5.9) with a fake embedding and the category filter (skipped without chromadb).

On Databricks serverless (copy of `track/pydyna` after merging `main`): full suite 310 passed, 5 skipped (the 5 real-data tests of T1.0); `tests/pydyna` 39 passed, 5 skipped. Removing the category filter makes 1 test fail; removing the PyDPF-Post filter makes 1 fail.

Not checked here: the real database, the real model and the real categories (data only on Linux), and a full chat with a model.

## Verify (Linux worktree; the searches run on a copy of the database)

```bash
source ~/.ansa_meta_copilot/.venv/bin/activate
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna
git pull
python -m pytest -q tests/pydyna -rs   # 44 passed, 0 skipped
python -m pytest -q                    # 315 passed
rm -rf /tmp/pydyna_vdb && cp -r knowledge/pydyna/vector_db /tmp/pydyna_vdb
HF_HUB_OFFLINE=1 python - <<'EOF'
import time
from collections import Counter
from pathlib import Path
from champion.specialists.pydyna import records as rec
from champion.specialists.pydyna.search import ChromaSearch

cats = Counter(r.get("category", "") for r in rec.load_keywords(Path("knowledge/pydyna/knowledge-base")).values())
print(len(cats), "categories:", cats.most_common())
s = ChromaSearch(Path("/tmp/pydyna_vdb"))
for title, coll, text, n, where in [
    ("material", "lsdyna_keywords", "piecewise linear plasticity for steel", 5, {"category": "material"}),
    ("contact", "lsdyna_keywords", "self contact of a crushing rail", 5, {"category": "contact"}),
    ("api", "pydyna_api", "create a deck with a shell part", 5, None),
    ("dpf_post (before the filter)", "pydpf_post", "section force from binout", 10, None)]:
    start = time.monotonic()
    hits = s.query(coll, text, n, where)
    print(f"{title}: {time.monotonic() - start:.2f} s")
    for h in hits:
        m = h.metadata
        print(f"  {h.distance:.4f}  {m.get('keyword') or m.get('source_path')}  [{m.get('category', '')}]"
              f"  keep={rec.keep_code_path(m.get('source_path', '')) if coll == 'pydpf_post' else rec.keep_api_path(m.get('source_path', '')) if coll == 'pydyna_api' else ''}")
EOF
```

## Fix after the Linux search check (21 Nov 2026)

The first run on the real database showed `pydyna_api` hits only from `tests/` and `codegen/manifest.json`, and the PyDPF-Post filter keeping `tests/`. User decision (option A): both code searches keep only `src/`, `doc/`, `docs/`, `examples/` (`records.keep_code_path`; `keep_api_path` also keeps the 5 docs records without a source file). As the old database still holds those files, both searches ask Chroma for 5 x n (at most 100) and return the first n kept; fewer than n come back when the top 100 hold fewer. Tests: 11 more (filter cases, API documents without tests and `codegen/`, API search drops them); serverless 321 passed, 5 skipped. Removing the folder check makes 8 tests fail; an API search without the filter makes 1 fail.

Expected (rerun after the fix): `keep=` shows `True` or `False` for `pydyna_api` and `pydpf_post` hits (the script prints raw hits, before the filter); `material` and `contact` among the categories; the material and contact hits only of that category; API hits with a source path; the model loads without internet. Report the whole output; the first search includes loading the model.
