# Step 2 - Access levels, policy and generic file tools

## What was built

| File | Purpose |
| --- | --- |
| `champion/policy/levels.py` | `AccessLevel` and the five levels (1.1, 1.2, 2.1, 2.2, custom with `extra_dirs`) |
| `champion/policy/sensitive.py` | Paths blocked at every level: `/proc`, `/sys`, `/dev`, `/root`, protected system files, secret-looking names; for writing also `/etc`, `/usr`, `/opt` ... (ported from mChampion and extended) |
| `champion/policy/scope.py` | `check_path` (blocked paths, no symbolic links, level and place) and `open_no_symlinks` |
| `champion/policy/tool_policy.py` | Tool-policy registry: every tool declares its right (`none`, `read`, `write`, `execute`); no entry, no run |
| `champion/storage/audit.py` | Audit log: one SQLite row per tool call (file mode 600) |
| `champion/tools/guard.py` | `run_guarded` (policy, level, thread, BLOCKED/ERROR text, output cap, audit) and `requires()` for `is_enabled` |
| `champion/tools/filesystem.py` | `filesystem_list`, `filesystem_read`, `filesystem_disk_usage` |
| changed | `core/context.py` (level and audit in the context), `core/runtime.py`, `tools/session_info.py`, `agents/assistant.py`, `chat.py` (`/level`, `/audit`), `cli.py` (`--level`) |
| tests | `tests/test_policy.py`, `tests/test_filesystem_tools.py` (47 new, 103 in total) |

## Key concepts

**Where the security lives.** The model only chooses tool arguments; it cannot see or change the run context. The access level is in the context, and every tool checks it *inside* the tool, on every call. Prompts ("never read secrets") help the model behave, but the checks in code are what enforce it.

**Order of checks in `check_path`.** Normalise the path (relative to the working folder, `..` resolved as text) -> fixed blocks (system folders, secret names) -> no symbolic link anywhere in the path -> the level allows the right -> the path is inside the allowed area. The working folder itself is resolved once at start, so a linked home folder still works.

**Why links are refused, not followed.** `work/data -> /home/u/.ssh` would let a read inside the working folder reach a secret. Refusing links is simple and leaves no room for mistakes. `open_no_symlinks` then opens the path one folder at a time with `O_NOFOLLOW`, so a link swapped in after the check is still refused (tested).

**`is_enabled` vs. the check in the tool.** `requires("read")` hides read tools from the model when the level gives no read right (a custom level with `read: none`); the model then does not even see them. This keeps the prompt honest, but it is not the security boundary: `run_guarded` checks again.

**Deny by default.** A tool without a policy entry is refused. This is what later lets the registry check every specialist and MCP tool before it is offered.

**BLOCKED and ERROR results.** A refused call returns `BLOCKED: <reason>` to the model (and the chat shows it). The instructions tell the agent to explain the reason, point to `/level` when a level would allow it, and never try to work around a block.

**Content checks.** `.key` is the LS-DYNA extension, so `.key` files are read only if they contain keyword lines; any file with a `PRIVATE KEY` block is refused; binary files and files of 100 MiB or more are refused (large models get their own tools in step 6).

**Audit.** Every call (allowed, blocked or failed) writes one row: time, agent, tool, arguments, level, decision, reason, result size. Results themselves are not stored. `/audit` shows the rows of the current chat.

## Code flow of one file read

1. The model calls `filesystem_read({"path": "/etc/hostname"})`.
2. The SDK checks `is_enabled` (read allowed at the level) and calls the tool with `ctx`.
3. `run_guarded`: policy entry `read` -> level allows read -> `read_text_file` in a thread.
4. `check_path`: not blocked, no link, but at 1.1 outside the working folder -> `PolicyDenied`.
5. `run_guarded` returns `BLOCKED: ... outside the working folder ...`, writes an audit row `block`.
6. The model explains; the user types `/level 2.1`; the same call now returns the file.

## How to verify

```bash
pytest                                    # expect 103 passed
champion chat --level 1.1
```

In the chat:
* "What files are in this folder?" -> `filesystem_list`; `.env` and other secret-looking names are hidden.
* "Show the first lines of <a model>.k" -> `filesystem_read` returns the start of the file.
* "Read /etc/hostname" -> `BLOCKED ... outside the working folder`; `/level 2.1`, ask again -> it works.
* "Read /proc/self/environ" or ".env" -> blocked at every level, also at 2.2.
* `/audit` -> the allowed and blocked calls with their reasons.
* Put a symbolic link in the folder and ask to read through it -> refused.

## Verified here

Databricks serverless (Python 3.12, openai-agents 0.24.0): 103 tests passed; a scripted chat showed list, a blocked read at 1.1, `/level 2.1`, the same read allowed, `/proc` blocked at 2.1, and the `/audit` rows. A run with a real model and your files is still to be checked on the Linux machine.
