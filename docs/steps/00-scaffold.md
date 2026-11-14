# Step 0 - Skeleton, configuration and `champion doctor`

## What was built

| File | Purpose |
| --- | --- |
| `pyproject.toml` | Package definition, dependencies, the `champion` command (`champion.cli:main`), extras `pydyna` and `dev` |
| `config/config.example.yaml` | Every config key with its default and a comment |
| `champion/core/config.py` | Builds the configuration: defaults + YAML file, normalise, resolve paths, validate; reads the OpenAI key |
| `champion/doctor.py` | The checks behind `champion doctor` |
| `champion/cli.py` | Command line: `--version`, `--config`, `doctor [--online]` |
| `tests/test_config.py`, `tests/test_doctor.py` | 40 tests |

No agent and no OpenAI call yet (except `doctor --online`).

## Key concepts

**Defaults + override.** `DEFAULTS` in `config.py` holds every key. The YAML file only needs the keys you change; `_merge` copies them over the defaults. So code can always read `config.get("pydyna.large_file_bytes")` without checking whether the key exists.

**Fail early, with a clear message.** `validate()` collects all problems (wrong level, write scope wider than read, `large_file_bytes` above `max_model_bytes`, `true` where a number is expected, ...) and `load_config` raises one `ConfigError` listing them. Unknown keys (typos) become warnings, shown by `doctor`. Sections where new names are expected (`agents`, `mcp.servers`, `kb.sources`) accept any key, so the future ANSA/META and PRIMER agents need no change here.

**Two kinds of relative path.** `execution.working_directory` is relative to the folder you start Champion from (that is the folder you work in). Storage, PyDyna and kb paths are relative to the project root, so they do not change when you start Champion from another folder.

**The key stays out of everything.** `get_openai_api_key` reads `OPENAI_API_KEY` from the environment, else from `.env` with `dotenv_values`, which does not put it into `os.environ`. That matters from step 8: generated code runs in a child process, and a child inherits `os.environ`. Doctor only says where the key was found; the online check reports the error type and HTTP status, never the error message (which can echo request details).

**Checks never crash the doctor.** Each check returns `CheckResult(name, status, detail)`; `run_checks` catches a crashing check and reports it as FAIL, so one broken check does not hide the others. Status meanings: FAIL = Champion cannot work; WARN = fine for now, needed in a later step.

## Code flow of `champion doctor`

1. `cli.main` parses arguments.
2. `load_config(args.config)`: `find_config_file` -> YAML -> `_merge` -> `_normalise` -> `_resolve_paths` -> `validate`. Invalid config: message and exit code 2.
3. `doctor.run_checks`: Python version, config (file, unknown keys, model placeholder, level, tracing), key (and `.env` file mode), packages (`importlib.util.find_spec`, no import), paths (working dir, writable storage, vector DB, keyword reference, PyDyna Python), optional online check.
4. `doctor.render` prints a table; exit code 1 if any FAIL.

## How to verify

```bash
pip install -e '.[dev]'
pytest                                   # expect 40 passed
champion --version                       # champion 0.1.0
champion doctor                          # without config.yaml: WARN config file, FAIL model and key
cp config/config.example.yaml config/config.yaml   # set llm.model_id
echo 'OPENAI_API_KEY=sk-...' > .env && chmod 600 .env
champion doctor                          # Model OK, key "found in .env (value not shown)"
champion doctor --online                 # one API call: key accepted, model available
```

Things worth trying:
* Set `default_level: '3.1'` -> exit code 2 with the error listed.
* Add a typo key such as `llm: {modle_id: x}` -> WARN "Unknown config key".
* `chmod 644 .env` -> WARN about `.env` permissions.
* Set `pydyna.database_path` to your existing PyDyna_Agent_git `vector_db` -> "PyDyna vector DB" becomes OK.

## Verified here

On Databricks serverless (Python 3.12, openai-agents 0.24.0, openai 3.51.0, textual 1.0.0): package build OK, 40 tests passed, `champion doctor` with the example config showed the expected FAIL for model and key and WARN for the PyDyna paths. The terminal UI and your real paths still need checking on your Linux machine.
