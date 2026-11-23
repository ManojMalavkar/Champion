# Track T3 - ANSA/META

Branch `track/ansa-meta`, package `champion/specialists/ansa_meta/`, tests `tests/ansa_meta/`, knowledge `knowledge/ansa_meta/`. Status: plan written 23 Nov 2026 after a review of ANSA-META_CodeRAG (same approach as track T1 PyDyna); D1-D4 decided by the user 23 Nov 2026; nothing built yet. The branch is still at M0 (`909bf6d`); `main` is 12 commits ahead (CR-1, CR-2 memory, M6 hook), fast-forward possible.

## Goal
One group `ansa_meta_agent` with sub-agents (user, 20 Nov 2026): Code (writes ANSA and META Python scripts) and Task (runs tested scripts in batch to extract information or do jobs). Knowledge and tools ported from ANSA-META_CodeRAG (read only there).

## Mapping to the PyDyna track (same pattern)

| PyDyna (T1) | ANSA/META (T3) | Needs |
| --- | --- | --- |
| T1.0 records | T3.0 records | nothing |
| - (PyDyna data cannot be rebuilt) | T3.0b versioned knowledge builder in Champion | T3.0 |
| T1.1a tools on a search stub | T3.1a tools on a search stub | T3.0 |
| T1.2a API catalog from installed ansys-dyna-core | T3.2a API catalog from the BETA autocomplete stubs (`py_dev/pydev_ansa`, `pydev_meta`, 24.1.6) | T3.0 |
| T1.2b API signature check | T3.2b script check (unknown symbols, wrong arguments, deprecated calls, forbidden modules) | T3.2a |
| T1.1 / T1.2 switch to M1 | T3.1 switch to M1 | M1 |
| T1.4 run with approval | T3.3 batch backends | M2, M3, executables, licences |
| - | T3.4 Task sub-agent | T3.3 |
| T1.6 skills | T3.5 skills | T5 |

## Working flow
Code, tests and plans in the `Champion-ansa-meta` Git folder. Champion keeps its own copy of the knowledge data (D2), one folder per ANSA/META version:

```
knowledge/ansa_meta/<version>/
  MANIFEST.json        version, source docs folder, embedding model, counts, SHA-256 per file
  knowledge-base/      records JSONL, kg_nodes/kg_edges JSONL, session_commands.json
  vector_db/           Chroma, built on Linux only (D4)
```

The active version is chosen in the `ansa_meta` config section (`version: "24.2.6"`), so v25 / v2025 is added next to it, not over it. As for T1, nothing under `knowledge/` is committed for now; `vector_db/` is excluded locally. The JSONL files are in the ANSA-META_CodeRAG GitHub repo, so real-data tests for the records can also run here on serverless (env `CHAMPION_ANSA_META_KB`); building and searching the vector DB is checked on Linux by the user's local Codex agent. Tests that need data skip when it is missing.

## Source of the port (read only)
Repo https://github.com/ManojMalavkar/ANSA-META_CodeRAG (Databricks Git folder `ANSA-META_CodeRAG`, head `53035a8`). Built from `/nobackup/people/t0018k6/script/Github_repo/LLM/SafetyAgent/01_ANSA_ApiAgent` (paths in `knowledge_manifest.json`).

| File | Records | Size | Used by |
| --- | --- | --- | --- |
| `coderag_documents.jsonl` | 23491 symbols (function 18869, attribute 1981, variable 1677, class 645, constant 314, none 5); 2925 deprecated | 48.7 MB | `build_vector_db.py` -> collection `ansa_api` |
| `kg_nodes.jsonl` / `kg_edges.jsonl` | 25000 nodes / 71768 edges (USES_TYPE 33138, RETURNS 10909, HAS_METHOD 10069, BELONGS_TO 7902, SEE_ALSO 6822, DEPRECATED_BY 2820, USES 108) | 2.4 + 7.3 MB | `kg_retriever.py` (NetworkX) |
| `kg_cache.pkl` | pickle of the graph | 7.8 MB | `kg_retriever.py` |
| `session_commands.json` | META session command tree, version 26.10.000 | 20.2 MB | `session_tools.py`; `build_session_vector_db.py` -> `meta_session_commands` |
| `coderag_examples.jsonl` | 9 example scripts | 26 KB | not indexed |
| `html/` | BETA API docs + autocomplete stubs (`_downloads/autocomplete/py_dev`, `vsc_json/{ansa,meta,guitk,komvos}.json`) | 110 MB | source of the records |

* Embedding `BAAI/bge-small-en-v1.5` on CPU (same as PyDyna), cosine collection `ansa_api`; one collection, not `ansa_api` + `meta_api` as PLAN.md M1/T3.1 says (to correct on `main`).
* 9 tools in `bin/tool_functions.py` + `bin/session_tools.py`: `search_api`, `lookup_symbol`, `get_class_methods`, `get_deprecated_replacement`, `list_module_symbols`, `search_session_command`, `search_session_semantic`, `parse_session`, `parameterize_session`.
* Not ported: 4 LLM backends, Gradio apps, JSONL/SQLite logging, token usage, analytics, log agent, admin tools, improve_knowledge loop, `config_secrets.py` (Champion runtime, audit, memory and TUI cover these).

## Problems found in the source (to fix in the port)
Checked on the real files on serverless, 23 Nov 2026.
1. Duplicate symbols: 8909 records without product prefix (from `vsc_json`, e.g. `plot2d.Curves`) also exist with prefix (from the HTML reference, `meta.plot2d.Curves`), with different text; 835 unprefixed names exist under both `ansa.` and `meta.` (e.g. `base.BCSettingsSetValues`, `utils.Messenger.echo`), so the bare name is ambiguous. Port: one canonical record per product + dotted name (product from the source file: `ansa.json`, `meta.json`, `guitk.json`, `komvos.json`, `pydev_ansa`, `pydev_meta`, HTML path); merge HTML signature with stub docstring; keep the bare name as alias.
2. Non-API symbols from example scripts and private names (`setup.*`, `__init__.*`, `BCGUI_*`, `licensing_example._*`; 153 private `_` names). Port: drop private names; example-script symbols become example records, not API.
3. Text noise: `&nbsp;` in 8165 docstrings, 527 without docstring, 6985 without description, 581 without signature, 5 without type. Port: clean entities, fill description from the first docstring line, type from the KG node.
4. Deprecated: 2925 flagged, 2820 `DEPRECATED_BY` edges, only 1145 with a "Use ..." text. Port: replacement from the edge, else from the text, mapped to the canonical name; report "no replacement known" otherwise.
5. Versions: the docs in the repo say 24.1.6 (`index.html`, `py_dev_autocomplete_24.1.6.tar.gz`), the session tree 26.10.000; the installed version is 24.2.6 (user, 23 Nov 2026). Port: version on every record and in the MANIFEST; the T3.0b builder rebuilds from the 24.2.6 docs on Linux.
6. Scores: `search_api` returns `1 - distance` (cosine), `search_session_semantic` `1 - d/2` (L2 assumption). Port: raw distance and score with source file (same fix as T1 item 5).
7. `lookup_symbol` silently returns the nearest neighbour when the exact name is missing. Port: error with close names (as T1.2a `lookup_api`).
8. `_ensure_writable` chmods the shared `vector_db/` to 777 on every start; `kg_cache.pkl` is loaded with pickle. Port: no chmod, open read only or on a copy (M1 requirement), graph built from the JSONL files, no pickle.
9. Sources are absolute `/nobackup/...` paths. Port: paths relative to the knowledge root.
10. `examples` is empty in all 23491 records; code examples are only in `notes` (7146). Port: map `notes` to examples.

## Sub-steps
- [ ] T3.-1 Bring the track up to date: fast-forward `track/ansa-meta` to `main` (`79b9fa5`). Verify: full suite passes here as on `main`. Needs the user's OK to push.
- [ ] T3.0 Data and records (no M1): `champion/specialists/ansa_meta/records.py` (load JSONL and the session tree, fixes 1-5, 9, 10, stable ids `<product>:<dotted name>`, record -> document text and metadata as in `build_vector_db.py`); fixtures in `tests/ansa_meta/fixtures/`; `tests/ansa_meta/test_ansa_meta_data.py` on the real files (counts, no duplicate canonical ids, every deprecated record has a replacement or a reason, `meta.plot2d.Curves` merged with its alias). Verify: unit tests and real-data tests pass here.
- [ ] T3.0b Versioned knowledge builder (D2, D4): port the deterministic part of `bin/ingest.py` (`KnowledgeExtractor`: autocomplete JSON, autocomplete Python stubs, HTML reference, signature fill, validate, knowledge graph; no LLM is used there) and `build_session_vector_db.py` into `champion/specialists/ansa_meta/build.py`, with the T3.0 fixes applied during the build. Input: a BETA API docs folder of any version (HTML + `_downloads/autocomplete/*.tar.gz` or extracted) and the session command tree; output: `knowledge/ansa_meta/<version>/` with MANIFEST. Two stages: `records` (pure Python, runs anywhere) and `index` (Chroma + embedding, Linux only, offline model). Entry point `python -m champion.specialists.ansa_meta.build --version 24.2.6 --docs <dir> --sessions <file> [--index]`; a `champion knowledge build` command is a core request (CR, `cli.py` shared). Verify: here, a build of the repo docs (24.1.6) gives the T3.0 counts; on Linux, a 24.2.6 build + index, MANIFEST counts equal the collection counts, and a second build gives the same ids and hashes. Open: where the 24.2.6 docs and the session tree come from on Linux (path; the tree came from the META VS Code extension).
- [ ] T3.1a Tools and group on a search stub: group `ansa_meta` with sub-agents `ansa_meta_code` (API and META session tools, D1) and `ansa_meta_task` (registered from T3.3); tools `search_api` (product, type, deprecated filters), `lookup_symbol`, `get_class_methods`, `get_deprecated_replacement`, `list_module_symbols`, `search_session_command`, `search_session_semantic`, `parse_session`, `parameterize_session`; graph from T3.0 without NetworkX if possible; search through a track-local `search.py` until M1; right `none` (they read only Champion's knowledge base). Verify: unit tests here; real searches here once a vector DB exists.
- [ ] T3.2a API catalog (no M1 or core change): exact records of modules, classes, methods and functions with parameters, types, defaults and returns, parsed with `ast` from the autocomplete stubs (24.1.6) with version and source; tool `lookup_api`. Verify: `ansa.base.CollectEntities`, `meta.plot2d.Curves` and an unknown name (error with close names).
- [ ] T3.2b Script check (uses T3.2a): unknown symbols, wrong or missing arguments, deprecated calls with the replacement, forbidden modules and calls, reported with line. Verify: a script with a wrong argument and a deprecated call is reported, a correct one passes, no run. Hooks into M3 `code_check` when M3 exists.
- [ ] T3.1 Switch to M1 (needs M1): remove `search.py`. Verify: same results as T3.1a.
- [ ] T3.3 Batch backends (needs M2/M3 and the executables and licences, PLAN P.8 decision 5): ANSA `-nogui -execscript`, META batch.
- [ ] T3.4 Task sub-agent: parts, thickness, mass, materials; META curves and reports into a run folder. Verify: part list of a small model.
- [ ] T3.5 Skills (needs T5).

## Decisions
* Executables and licence environment: not yet clear (open).
* User decisions 23 Nov 2026:
  * D1: META session tools go in the Code sub-agent (no third sub-agent).
  * D2: Champion keeps its own copy of the data, per version (`knowledge/ansa_meta/<version>/`); the data must be built for ANSA/META 24.2.6 now and for META v25 / v2025 later.
  * D3: installed version 24.2.6; update to v25 or v2025 within about 2 months.
  * D4: `vector_db/` is built on Linux only; the build method is part of Champion (T3.0b) so new versions can be built with it.
* Proposed: Task sub-agent registered only from T3.3 (no tools before).
* Proposed: knowledge files not in Git (as T1); confirm.

## Requirements for M1 from this track
Same as T1 (MANIFEST, offline embedding model, read-only open, raw distance with source, track-supplied record -> document function), plus: a JSON tree source (session commands) next to JSONL, and a small graph store (nodes, edges) built from JSONL without pickle.

## Core requests (CR)
None yet. Expected: the `ansa_meta` config section (as `pydyna` in `config.py` `DEFAULTS`).

## Pending local checks
* Path of the ANSA/META 24.2.6 API docs and of the META session command tree on Linux (needed for T3.0b).

## Findings and live checks
* 23 Nov 2026, serverless: data review above (counts, duplicates, deprecated, versions) on the `ANSA-META_CodeRAG` Git folder.
