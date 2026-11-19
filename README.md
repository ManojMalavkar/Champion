# Champion

Multi-agent CAE assistant (PyDyna / LS-DYNA and Linux) built on the OpenAI Agents SDK. Rebuild of mChampion, developed step by step; see `PLAN.md` for the steps and `docs/steps/` for a walkthrough of each one.

Current state: step 4e (configuration, `champion doctor`, `champion chat` with `session_info` and read-only file tools, access levels and an audit log, models through the Databricks Unity AI Gateway, Markdown answers in the terminal, orchestrator with a specialist plug-in interface, and the step 4a Linux tool layer: find, grep, disk and process tools, streaming `file_edit` and `files_organize` with `/changes` and `/undo`, and safe `shell_run` / `shell_pipeline`, used by the first specialist, Linux (`linux_coder_agent`, switch off with `agents.linux_coder.enabled: false`), and per-folder access: read or edit per folder, chosen by the user with `/access`).

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
champion chat                                   # interactive: /help, /level, /access, /audit, /changes, /undo, /new, /usage, /exit
champion chat -q "What time is it and which folder am I in?"
champion chat --plain                           # raw text answers (no Markdown rendering)
```

In a terminal, answers are rendered as Markdown (bold, lists, code, tables). When output goes to a file or pipe, or with `--plain`, the raw text is printed.

Tool calls are printed as `-> tool(arguments)` and `<- tool: N chars` (or the BLOCKED / ERROR reason). Only the last `orchestration.history_turns` turns (10) are sent with each question; `/new` starts a fresh conversation.

## Access

Access is a list: the working folder, any number of folders, and everything else, each with `none`, `read` or `edit` (read and change; deletes still ask every time). The longest matching folder wins. The old levels are presets of this list:

| Preset | Working folder | Everything else |
| --- | --- | --- |
| 1.1 (default) | read | none |
| 1.2 | edit | none |
| 2.1 | read | read |
| 2.2 | edit | edit |
| custom | from `policy.custom` (`extra_dirs` become folders) | |

```bash
champion chat --level 1.1 --read /data/models --edit /scratch/runs --outside none   # this chat only
champion access list                      # saved list; also add PATH read|edit|none, remove PATH|N, outside, working
```

In the chat: `/level 2.1` applies a preset and keeps your folders; `/access` shows the list; `/access add PATH read|edit|none`, `/access set N RIGHT`, `/access remove N`, `/access outside RIGHT`, `/access working read|edit`, `/access preset NAME`. Changes are for the session until `/access save`, which writes `~/.champion/access.yaml` (mode 600; `access.file` in the config, `null` = off). Edit access to `/`, the home folder, a parent of the working folder or everything else asks `Continue? [y/N]` first. Folders can also be listed in the `access` section of `config.yaml`. Order at start: preset, `config.yaml`, saved file, command line. Only the user changes access; the agents have no tool for it. Always blocked: `/proc`, `/sys`, `/dev`, `/root`, protected system files, secret-looking names (`.ssh`, `.env`, `*.pem`, tokens, passwords), private keys, symbolic links; OS folders such as `/etc` and `/usr` are never edited. Every tool call is recorded in the audit log (`storage.audit_db`); `/audit` shows the calls of the current chat.

## Safety notes

* The access list (presets 1.1-2.2 and custom, plus per-folder rights from step 4e) is enforced in every tool; BLOCKED messages name the entry and the `/access` command that would allow it. Every access change is in the audit log.
* Commands (step 4a) are never run through a shell: an allow-list of commands and exact options, every path checked like the file tools, a clean environment without tokens, time, CPU, memory and file-size limits. Edits where the list allows edit run without a prompt and are listed in `/changes` (`file_edit` and `files_organize` can be undone with `/undo`); deletes ask `Approve? [y/N]` every time and are refused in `champion chat -q`. `execution.max_command_file_bytes` (default 8 GiB) limits files written by commands.
* SDK tracing is off by default; turning it on sends traces to OpenAI (refused with the Databricks provider).
* Content read by the agents is sent to the configured model: through the workspace AI Gateway (Databricks provider) or to OpenAI's API (OpenAI provider).
