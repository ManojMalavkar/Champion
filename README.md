# Champion

Multi-agent CAE assistant (PyDyna / LS-DYNA and Linux) built on the OpenAI Agents SDK. Rebuild of mChampion, developed step by step; see `PLAN.md` for the steps and `docs/steps/` for a walkthrough of each one.

Current state: step 4a (configuration, `champion doctor`, `champion chat` with `session_info` and read-only file tools, access levels and an audit log, models through the Databricks Unity AI Gateway, Markdown answers in the terminal, orchestrator with a specialist plug-in interface, and the step 4a Linux tool layer: find, grep, disk and process tools, streaming `file_edit` and `files_organize` with `/changes` and `/undo`, and safe `shell_run` / `shell_pipeline`; the Linux specialist that uses them comes in step 4b).

## Install

```bash
cd Champion
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'          # add ,pydyna for the knowledge base (step 3)
```

Requires Python 3.10 or newer. Use a dedicated virtual environment: `openai-agents` 0.24 requires `openai` 3.x, which conflicts with packages that need `openai` 2.x (for example `langchain-openai` 1.4 used in the course labs).

## Configure

```bash
cp config/config.example.yaml config/config.yaml   # Databricks AI Gateway setup
echo 'DATABRICKS_TOKEN=<your token>' > .env && chmod 600 .env
```

The example config uses `llm.provider: databricks`: `system.ai` models (default `system.ai.claude-haiku-4-5`) through the workspace Unity AI Gateway (`llm.base_url` ending in `/ai-gateway/mlflow/v1`), with the token in `DATABRICKS_TOKEN`. Legacy `/serving-endpoints` URLs are refused, and `llm.tracing` must stay false. For the OpenAI API instead, set `provider: openai`, `base_url: null`, `api_key_env: OPENAI_API_KEY` and an OpenAI model.

`llm.api_key_env` is the name of the environment variable holding your token or key (built-in default: `OPENAI_API_KEY`). The same name is used when reading `.env`; put only the variable name, never the key value, in the config. The environment takes precedence over `.env`. Champion never prints or logs the key and does not copy `.env` into the process environment. `config/config.yaml` and `.env` are git-ignored.

Config file lookup: `--config PATH`, else `CHAMPION_CONFIG`, else `config/config.yaml`. Keys you leave out use the defaults in `champion/core/config.py` (the example file lists all of them).

## Check the setup

```bash
champion --version
champion doctor            # local checks only
champion doctor --online   # one small API request per configured model
pytest
```

`champion doctor` exits with 0 when nothing failed, 1 when a check failed and 2 when the config is invalid.

## Chat

```bash
champion chat                                   # interactive: /help, /level, /audit, /changes, /undo, /new, /usage, /exit
champion chat -q "What time is it and which folder am I in?"
champion chat --plain                           # raw text answers (no Markdown rendering)
```

In a terminal, answers are rendered as Markdown (bold, lists, code, tables). When output goes to a file or pipe, or with `--plain`, the raw text is printed.

Tool calls are printed as `-> tool(arguments)` and `<- tool: N chars` (or the BLOCKED / ERROR reason). Only the last `orchestration.history_turns` turns (10) are sent with each question; `/new` starts a fresh conversation.

## Access levels

| Level | Read | Write |
| --- | --- | --- |
| 1.1 (default) | working folder | nothing |
| 1.2 | working folder | working folder |
| 2.1 | working folder and outside | nothing |
| 2.2 | working folder and outside | working folder and outside |
| custom | `policy.custom.read` (+ `extra_dirs`) | `policy.custom.write` |

Choose with `champion chat --level 2.1` or `/level 2.1` in the chat (`/level` alone shows the current one). Always blocked: `/proc`, `/sys`, `/dev`, `/root`, protected system files, secret-looking names (`.ssh`, `.env`, `*.pem`, tokens, passwords), private keys, symbolic links; OS folders such as `/etc` and `/usr` are never written. Every tool call is recorded in the audit log (`storage.audit_db`); `/audit` shows the calls of the current chat.

## Safety notes

* The access levels (1.1, 1.2, 2.1, 2.2, Custom) are enforced in every tool (step 2).
* Commands (step 4a) are never run through a shell: an allow-list of commands and exact options, every path checked like the file tools, a clean environment without tokens, time, CPU, memory and file-size limits. Writes at a write level run without a prompt and are listed in `/changes` (`file_edit` and `files_organize` can be undone with `/undo`); deletes ask `Approve? [y/N]` every time and are refused in `champion chat -q`. `execution.max_command_file_bytes` (default 8 GiB) limits files written by commands.
* SDK tracing is off by default; turning it on sends traces to OpenAI (refused with the Databricks provider).
* Content read by the agents is sent to the configured model: through the workspace AI Gateway (Databricks provider) or to OpenAI's API (OpenAI provider).
