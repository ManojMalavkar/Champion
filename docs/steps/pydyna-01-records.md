# Step T1.0 - PyDyna knowledge records

First step of track T1 (docs/tracks/pydyna.md). It needs no shared change and no M1: it adds a pure-Python record layer that reads the PyDyna knowledge-base JSONL files and turns every record into the id, text and metadata that the knowledge layer (M1) will index. No agent, tool or config section is added; Champion behaves as before.

## Source

Ported from the local PyDyna_Agent (`sacreate.py`, the builder that made the working database; `bin/build_vector_db.py` is outdated). Document text and metadata are the same as there, so search behaves as before. Data: `knowledge/pydyna/knowledge-base/` in the Linux worktree only, not in Git (user, 21 Nov 2026).

## What was built

* `champion/specialists/pydyna/__init__.py`: the track package (docstring only; T1.1 registers the group here). Found by M0 discovery, registers nothing.
* `champion/specialists/pydyna/records.py`:
  * `read_jsonl`: a bad line is an error with file name and line number (the old index skipped it silently).
  * `load_keywords`: generated schema first, curated reference merged over it (`merge_keyword`). All generated fields stay; for a field in both, curated values win key by key; the curated description wins, the generated text stays as `schema_description`; aliases are joined; `sources` says where a record came from.
  * `keyword_index`: names and aliases; an alias never hides a real keyword.
  * `keyword_documents`, `api_documents`, `dpf_post_documents`, `collection_documents`: stable ids `kw:<keyword>`, `api:<source_path>#<chunk>` or `api:doc:<class>.<method>`, `dpfpost:<source_path>#<chunk>`; a duplicate id is an error.
  * `keep_code_path` (both code collections) and `keep_api_path` (`pydyna_api`): keep `.py`, `.rst`, `.md` under `src/`, `doc/`, `docs/`, `examples/` only (user, 21 Nov 2026, option A); drop `tests/`, `codegen/`, files at the top of the repository (`README.md`, `pyproject.toml`), dot files and folders (`.pre-commit-config.yaml`, `.github/`) and repository files (`CODE_OF_CONDUCT`, `CONTRIBUTING`, `LICENSE`, ...). The 5 PyDyna docs records have no source file and are kept. (First version checked only suffix, dot folders and meta names: it kept `tests/` and `README.md`; changed after the Linux search check.)
  * `check_params`: required fields, types (`int`, `float`, `string`; a bool is never a number) as errors; unknown fields and `min`/`max` as warnings.

## Fixes against the old agent

| Old behaviour | Now |
| --- | --- |
| `*MAT_024` lookup returned only the 5 curated fields | full card plus curated units, limits, aliases |
| ids `kw_0`, `api_0`, `dpfpost_0` change with input order | ids from names and paths |
| PyDPF-Post indexed `.pre-commit-config.yaml`, `CODE_OF_CONDUCT.md` | repository files left out |
| `validate_keyword_params` did not check types | `check_params` does |

## Tests

* `tests/pydyna/test_pydyna_records.py` (25 tests) on small sample files in `tests/pydyna/fixtures/knowledge-base/` (made up, a few lines each): merge, input records unchanged, curated-only keyword, aliases, document text and metadata as in `sacreate.py`, ids the same when the input order is reversed, single source file instead of parts, PyDPF-Post filter, duplicate id, bad JSON line, missing files, parameter check.
* `tests/pydyna/test_pydyna_data.py` (5 tests) on the real data: line counts (3685, 5, 5, 62323, 1719), keywords merge to 3686 with 4 merged and `*DATABASE_D3PLOT` curated only, `*MAT_024` has more than 5 fields with unit, card and alias, 62328 API documents with unique ids, PyDPF-Post keeps fewer than 1719 and no repository files. Skipped when the data is missing; `CHAMPION_PYDYNA_KB` points to another copy.
* On Databricks serverless: 294 passed, 5 skipped (269 earlier + 25). The API data test also passed on the Git copy of PyDyna_Agent (`CHAMPION_PYDYNA_KB`, 62328 unique ids); the other 4 data tests need files that exist only on Linux. Replacing the merge by the old replace makes 3 tests fail; removing the type check makes 1 fail.

## Verify (Linux worktree, read only)

```bash
source ~/.ansa_meta_copilot/.venv/bin/activate
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna
git pull
python -m pytest -q -rs tests/pydyna/test_pydyna_records.py tests/pydyna/test_pydyna_data.py   # 30 passed, 0 skipped
python -m pytest -q   # no failures (full suite with the tools: see docs/steps/pydyna-02-tools.md)
python -c "from champion.specialists.pydyna import records as r; d=r.dpf_post_documents('knowledge/pydyna/knowledge-base'); print(len(d))"
```

Report the PyDPF-Post count (kept of 1719) and any failure in full.
