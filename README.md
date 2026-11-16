# Champion

Multi-agent CAE assistant (PyDyna / LS-DYNA and Linux) built on the OpenAI Agents SDK. Rebuild of mChampion, developed step by step; see `PLAN.md` for the steps and `docs/steps/` for a walkthrough of each one.

Current state: step 1 (configuration, `champion doctor`, and a first agent in `champion chat` with one tool, `session_info`).

## Install

```bash
cd Champion
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'          # add ,pydyna for the knowledge base (step 3)
```

Requires Python 3.10 or newer.

## Configure

```bash
cp config/config.example.yaml config/config.yaml   # then set llm.model_id
echo 'OPENAI_API_KEY=sk-...' > .env && chmod 600 .env
```

Set `llm.api_key_env` in `config/config.yaml` to the name of the environment variable holding your key (default: `OPENAI_API_KEY`). The same name is used when reading `.env`; put only the variable name, never the key value, in the config. The environment takes precedence over `.env`. Champion never prints or logs the key and does not copy `.env` into the process environment. `config/config.yaml` and `.env` are git-ignored.

Config file lookup: `--config PATH`, else `CHAMPION_CONFIG`, else `config/config.yaml`. Keys you leave out use the defaults in `champion/core/config.py` (the example file lists all of them).

## Check the setup

```bash
champion --version
champion doctor            # local checks only
champion doctor --online   # plus one API call to check the key and model
pytest
```

`champion doctor` exits with 0 when nothing failed, 1 when a check failed and 2 when the config is invalid.

## Chat

```bash
champion chat                                   # interactive: /help, /new, /usage, /exit
champion chat -q "What time is it and which folder am I in?"
```

Tool calls are printed as `-> tool(arguments)` and `<- tool: N chars`. Only the last `orchestration.history_turns` turns (10) are sent with each question; `/new` starts a fresh conversation.

## Safety notes

* The access levels (1.1, 1.2, 2.1, 2.2, Custom) are configured from step 0 but enforced from step 2.
* SDK tracing is off by default; turning it on sends traces to OpenAI.
* With a direct OpenAI key, content read by the agents is sent to OpenAI's API.
