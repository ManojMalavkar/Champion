# CR-2 - Memory hook (M6) on `main`

Core request from track T8 Memory (`docs/tracks/memory.md`). Built on `main`
21 Nov 2026.

## What was built

1. The memory library of `track/memory` (T8.1-T8.4, commit `a35412d`) is on
   `main`, unchanged: `champion/memory/` (storage, L0 `runtime_state`,
   L1 `working`, L2 `summaries`, L3 `preferences`, `separation`) and
   `tests/memory/` (50 tests). Walkthrough: `docs/steps/t8-memory-l0-l3.md`.
2. Two new files connect it to the core:
   * `champion/memory/hook.py` - `MemoryHook`, the only object the core
     calls. It is `ChampionContext.memory`; `None` when memory is off.
     Every memory error is caught there and kept in `problems`; a memory
     problem never fails a turn and never changes a tool result.
   * `champion/memory/commands.py` - `memory_command(runtime, argument)`
     for `/memory` (the TUI can call the same function).
3. Nine shared files, each with a small change:

| File | Change |
| --- | --- |
| `core/config.py` | Section `memory` (below), validated with the library's `WorkingSettings` ranges; `user_root` `~` = real home, `project` relative to the current folder |
| `config/config.example.yaml` | The same section, `enabled: false` |
| `core/context.py` | Field `memory` (the hook) - one field instead of the three drafted in the CR (runtime state, working memory and project folder are on the hook) |
| `core/runtime.py` | Session callback from the library instead of `keep_last_turns`; `ask()` starts an L0 request and ends it as `done`, `failed` (any error, also `MaxTurnsExceeded`) or `cancelled` (Ctrl-C); after the turn, dropped turns are summarised; `new_session()` clears working memory |
| `storage/audit.py` | `record()` returns the row ID |
| `tools/guard.py` | After the audit row: tool call with `audit_id` and agent name into L0; paths and IDs of the arguments into L1 |
| `execution/approval.py` | Before the prompt: open approval in L0 (`awaiting_approval`) and L1; after the answer (also on Ctrl-D / Ctrl-C = no): answer stored, L1 entry removed |
| `agents/orchestrator.py` | `with_current_level` adds the memory blocks: preferences for every agent; summaries and working memory only for the orchestrator (`Champion`); with a fixed memory rule |
| `chat.py` | `/memory`; `/new` also clears working memory; unfinished requests of the working folder listed at start |
| `doctor.py` | One line `Memory` (off, or on with layers and the user folder; FAIL if the folder cannot be written) |

## Config

```yaml
memory:
  enabled: false            # first merge: off; memory off = chat exactly as before
  max_turns: 8              # replaces orchestration.history_turns when on
  max_tokens: 16000
  tool_output_chars: 2000
  full_tool_turns: 2
  runtime_state: true       # <working folder>/.champion/runtime/
  keep_archived: 50
  summaries: true
  preferences: true
  user_root: ~/.champion/memory
  project: null
```

Open points of the CR, as built (can be changed later):
* `enabled: false` for the first merge.
* Summary model: `llm.model_id` (the same model object as the chat, one call
  without tools, tracing as the main run). Its tokens are added to the turn.
* Summary right after the turn (not in the background). Each dropped turn is
  summarised once, also when the summary fails (no retry every turn).

## Code flow of one turn (memory on)

```text
ask(text)
  memory.start_request      L1: paths/IDs of the text   L0: REQ-YYYYMMDD-NNNN.json, step planning
  Runner.run
    session callback        L1 select_history: last max_turns turns, older tool outputs cut,
                            token budget; dropped turns remembered
    instructions            with_current_level + memory rule + L3 + L2 + L1 blocks
    tool call -> guard      audit row id -> L0 tool_calls[audit_id]; arguments -> L1 entities
      approval ask          L0 awaiting_approval + L1 pending -> prompt -> answer stored
  finally: memory.finish_request(done | failed | cancelled)  -> runtime/archive/ (no result excerpts)
  memory.summarize_dropped  L2 SUM-YYYYMMDD-NNN.json (user folder or project folder)
```

## `/memory` commands

`/memory`, `/memory help`, `show`, `prefs`, `keys`, `set KEY VALUE`,
`forget KEY`, `summaries`, `done ID#N`, `reject ID#N`,
`project [FOLDER|off]` (needs edit access to FOLDER), `unfinished [close]`,
`clear`. Only the user changes memory here: preferences are stored with
source `user`; `destructive_action_confirmation` cannot be set to false;
values that look like secrets are refused.

`/memory unfinished close` marks requests that never ended as failed. It is
not done automatically at start, because a second chat in the same working
folder may still be running them.

## Safety

* Memory off (default): no file is written, the history callback is the old
  `keep_last_turns`, instructions are unchanged (test).
* Secrets: tool arguments, results, errors, user text and summary input are
  redacted by the library; a text with a secret adds no entities at all
  (test scans every memory file).
* The memory never answers an approval; the notes say they never count as
  approval and that the latest user message wins.
* L0 files live in `<working folder>/.champion/`, which every agent tool
  refuses.

## Tests

`tests/test_memory_m6.py` (18 cases): memory off unchanged; 4 invalid
configs; paths; one turn = one request `done` with `audit_id`; `failed` and
`cancelled`; approval open during the prompt and stored as declined;
instruction blocks for orchestrator and specialist; history selection and
summary of dropped turns; failing summary keeps the turn and is not retried;
secrets not stored; `/memory` commands and `/new`; unfinished requests at
chat start and `close`; doctor line; sub-agent name of nested calls; one
chat note when a summary fails.

Results (Databricks serverless, fresh copy): `main` 339 passed
(271 before + 50 library + 18). Mutation check: 12 changes that break a hook
(guard, approval, summary, failed-as-done, never finished, no runtime state,
no validation, memory always on, no `/memory`, no audit ID, `/new` keeps
working memory, specialist gets all blocks); 11 were caught at once. The
last one was missed first (the test called the hook directly); the
instruction test now renders through `with_current_level` for a specialist
agent, and the re-run catches it (1 failed, 17 passed). All 12 caught.

## Live check on Linux (22 Nov 2026)

OK: 339 passed; doctor line off/on; `/memory`, `set`, `prefs`; protected
preference refused (a secret value not checked yet: the pasted lines went to
the model as one message); declined `rm old.k` stored and the file
kept; archived requests `done` with `audit_id`, no `result_excerpt`; Ctrl-C
archived as `cancelled`; no secret in any memory file; folders 700, files 600.

Found:
* With no memory notes yet the model answered "Memory note set: ..." (it has
  no memory tool; nothing was saved). Fix: `SAVE_RULE` is always given to the
  orchestrator when memory is on (`test_orchestrator_is_told_it_cannot_save_memory`); 340 passed.
* After a killed chat, `REQ-20261122-0008.json` stayed in `runtime/` but
  `/memory unfinished` listed nothing. The file had 0 bytes, next to a
  `.tmp-*.json` of the same millisecond: the chat was stopped between
  claiming the ID (empty file) and the rename of its first write, and
  `unfinished()` skipped empty files for ever. Fix in the library
  (`runtime_state.py`, to be merged back into `track/memory`): an empty or
  unreadable request file older than 30 s is listed as unfinished (a younger
  one is a start in another chat that is still writing), and
  `/memory unfinished close` archives it as failed and removes `.tmp-*.json`
  files older than 5 minutes (first one hour: the live `.tmp` file of about
  30 minutes stayed); since the next live run such files are also removed
  at chat start, without a command (`test_old_temp_files_are_removed_at_start`; 342 passed)
  (`test_empty_request_file_of_a_killed_chat_is_listed_and_closed`, fails
  without the fix); 341 passed. Live: REQ-0008 listed at start and archived
  as failed.
* Open: that chat was not killed by the user. It ended right after the
  question with no "Bye." (every normal exit prints it) and no message, after
  writing the temp file and before its rename (`fsync` on `/d`). So it was
  stopped from outside (signal) or crashed hard. Not reproduced yet; checks:
  exit code, `logs/`, `dmesg`.
* After the fix the model says "I've noted ... for our conversation": no
  false claim of a saved memory note. Secret value refused by `/memory set`.

## How to verify (Linux)

```bash
source ~/.ansa_meta_copilot/.venv/bin/activate
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion
git pull
pytest -q                      # 339 passed
champion doctor                # line "Memory  OK  off (memory.enabled: false)"
champion chat --level 1.2      # unchanged; /memory says memory is off
```

Then set `memory.enabled: true` in `config/config.yaml` and check:
1. `champion doctor`: `Memory OK on: runtime state, summaries, preferences; /d/people/t0018k6/.champion/memory`.
2. `champion chat --level 1.2`, ask one question with a tool call; then
   `ls <working folder>/.champion/runtime/archive/` shows one `REQ-...json`
   with `current_step: done` and the tool call with `audit_id`.
3. `/memory set result_format table`, `/memory show` lists it; the next
   answer uses tables.
4. A delete (`delete old.k`), answer `n`: the archived request shows the
   approval `declined`.
5. More than 8 questions: `/memory summaries` lists items of the oldest
   turns; `/memory` shows no problems.
6. Ctrl-C during an answer: the request is archived as `cancelled`.
