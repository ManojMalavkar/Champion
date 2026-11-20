# Step 4d - Flexible editing

Status: 4d.1-4d.7 built (244 tests; 243 pass on serverless, the doctor exit-code test runs locally). Local check on the Linux machine
pending (list at the end).

## 4d.1 + 4d.2 - Several edits in one change

`file_edit` now takes a list of edits. Every edit is matched against the file
as it is before the call, so the order in the list does not change the result.

| Operation | Fields | What it does |
| --- | --- | --- |
| `replace` | `old_text`, `new_text`, optional `occurrence` | Exact block replaced (`new_text` "" deletes it). Must match once, or `occurrence` picks the match (1 = first). |
| `delete` | `old_text`, optional `occurrence` | Exact block removed. |
| `insert_before` / `insert_after` | `anchor`, `text`, optional `occurrence`, `idempotent` | `text` inserted next to the exact anchor. `idempotent: true` skips it when the text is already in the file. |
| `replace_lines` | `start_line`, `end_line`, `expected_text`, `new_text` | Lines replaced by number (1-based, inclusive). `expected_text` must be the current text of those lines, so a stale line number is refused. `new_text` "" deletes the lines. |

Example (one call, one change):

```json
{"path": "app.py", "edits": [
  {"operation": "insert_after", "anchor": "import pathlib\n", "text": "import logging\n"},
  {"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"},
  {"operation": "delete", "old_text": "LEGACY_MODE = True\n"}
]}
```

The old call `file_edit(path, old_text, new_text)` still works and is one `replace`.

### Rules

* All edits are checked before anything is written. If one fails (text not found,
  ambiguous, wrong field, stale lines), nothing changes and the message names the edit
  (`edit 2 (insert_after): anchor was not found ...`).
* Edits must not overlap; two inserts at the same place are refused (their order would
  be a guess). A replace that ends where an insert starts is fine.
* At most 20 edits, old text and anchors up to 64 KiB each, new text up to 1 MiB in total.
* The file keeps its encoding (UTF-8 or Latin-1) and line endings (LF or CRLF); binary
  files, secrets, links and `.champion` stay refused, and edit access is checked per folder.
* `dry_run: true` returns the diff and changes nothing.
* `expected_sha256` refuses the edit if the file changed since it was read; from 4d.4 an
  agent must give it. Every result gives the new SHA-256 (line `File SHA-256 now ...`, before the diff).

### One change, one undo

The blocks are written in file order in one streaming pass to a temporary file, then
one atomic replace (`text_patch.apply_hunks`). Memory stays about one chunk, so model
files up to 8 GiB work as before. The result shows one diff; `/changes` has one entry;
`/undo` takes the whole call back (refused if the file changed since).

### Approval

Low-risk edits run without a prompt. A higher-risk edit asks once (`Approve? [y/N]`,
default no; refused with `-q`):

* more than `edit.approval_max_deleted_lines` (100) lines deleted, or
* more than `edit.approval_max_changed_fraction` (0.2) of a file changed, for files of at
  least `edit.approval_min_bytes` (8 KiB). Small files use only the lines rule, so a short
  config can be edited without a prompt.

The prompt shows the file and a `change :` line with the reason and a summary.

### Audit

The audit row holds the operation names, character counts, and whether a hash or dry
run was given, never the file text.

### Tests

`tests/test_flexible_edit.py` (12 tests): four operations in one call and their undo,
all-or-nothing, overlaps, wrong fields, occurrence, idempotent insert, `replace_lines`
(chunk borders, last line without newline, delete), CRLF and Latin-1, undo with blocks
that grow and shrink, hash and dry run, approval yes/no/none, strict tool schema, and a
scripted chat through the Linux agent.

## The common pipeline

Every editing tool (file_edit, file_create, file_write, change_set_apply) goes through the
same steps in `tools/edit.py`, so they share every check:

1. `prepare_*`: path, access, links, secrets, file type, size, hash, anchors, overlaps,
   diff and risk. Nothing is written.
2. `stage_plan`: the complete new file in a temporary file next to the target (fsync).
3. `commit_plan`: atomic replace if the file is unchanged since step 1; a new file is put
   in place with a hard link, so a file created meanwhile is never overwritten.
4. `record_plan`: the `/changes` entry and the undo data.

## 4d.3 - file_create and file_write

| Tool | What it does | Asks? | Undo |
| --- | --- | --- | --- |
| `file_create(path, content, create_parents, dry_run)` | New text file (UTF-8). Never overwrites. Missing folders (up to 10) only with `create_parents: true`. | No | Removes the file (and the folders it created), only if the file is unchanged |
| `file_write(path, content, expected_sha256, dry_run)` | Replaces the whole content of an existing small file; keeps its encoding and line endings. `expected_sha256` is always required. | Always (whole-file replacement) | Restores the old content |

Both take at most `edit.max_write_bytes` (default 1 MiB, at most 10 MiB); `file_write`
also refuses existing files above that size (use `file_edit`). Content with NUL characters
or a private key is refused. The Linux agent has `file_create`; the Coder has both.

## 4d.4 - Hashes from reads, stale edits refused

* `filesystem_read` gives the SHA-256 on its second line (`SHA-256 <hex>`) and takes
  `start_line` to read a later part of a file (header `lines A-B of N`).
* `file_hash(path)` gives the SHA-256 of a file of any size up to 8 GiB (streaming), for
  large models that `filesystem_read` does not read.
* An agent's `file_edit` needs `expected_sha256` (not for `dry_run`). If the file changed
  since the read, the edit is refused ("read it again"); there is no automatic override.
  After an edit the agent uses the `File SHA-256 now` value for the next edit.

## 4d.5 - Multi-file change sets

```json
{"title": "Raise timeout", "files": [
  {"path": "app.py", "action": "edit", "expected_sha256": "...", "edits": [
    {"operation": "replace", "old_text": "TIMEOUT = 10", "new_text": "TIMEOUT = 30"}]},
  {"path": "tests/test_app.py", "action": "create", "content": "...", "create_parents": true},
  {"path": "settings.yaml", "action": "write", "expected_sha256": "...", "content": "..."}
]}
```

* Up to 20 files, each path once. Every file is prepared first; one problem and nothing
  changes (`file 3 (settings.yaml): anchor was not found ...; nothing changed`).
* One approval for the whole set if any file is higher-risk (whole-file write, many lines
  deleted, much of a file changed) or the set has more than `edit.approval_max_files` (10)
  files.
* All files are staged, then committed in order. If a commit fails, the files already
  written are rolled back (each only if still exactly as the set left it). A normal file
  system cannot change several files atomically: if the machine crashes during the commit
  phase, the files already written stay changed and are in `/changes`.
* `/changes` lists every file of the set with the title; `/undo` takes the whole set back.
  Before undoing a group, every entry is checked: if one file changed since, nothing is
  undone.

## 4d.6 - The Coder Agent

`coder_agent` (title Coder, `agents.coder.enabled`) changes code, tests, configuration
and documents: inspect (find, grep, `git_info`) -> read with hashes -> plan -> dry run ->
`file_edit` or `change_set_apply` -> check -> report. It has no `shell_run`.

| Tool | What it does |
| --- | --- |
| `code_check(paths)` | Syntax check of Python (compiled, never run), JSON, YAML and TOML files. |
| `run_check(name)` | Runs one check the user configured under `coder.checks`; the agent can only pick the name. Asks every time (approval class `check`), needs edit access, runs in the working folder with the executor limits (clean environment, time, CPU, memory, output). |
| `git_info(path)` | Read-only: branch, changed and untracked files, `git diff --stat`. Never commits, checks out or discards; fsmonitor, external diff and textconv are off. |

Example config for `run_check` (empty by default; interpreters stay blocked in `shell_run`):

```yaml
coder:
  checks:
    tests: [/path/to/venv/bin/python, -m, pytest, -q]
```

## 4d.7 - Hardening

`tests/test_coder.py` (19 tests) also covers: `.env`, `.champion`, `../` and paths outside
the access list; a link to an outside folder; a file created meanwhile (not overwritten);
a file changed between the check and the commit (refused, user's version kept); a 20 MiB
model in a change set (peak memory under 12 MiB); fsync failure while staging, disk full
and a failed commit with rollback (nothing changed, no temporary files left).

## Local check on the Linux machine

```
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion
git pull && python -m pytest -q          # expect 244 passed
cd /d/people/t0018k6/champ-test/work && champion chat --level 1.2
```

1. Create a small file first (for example `printf 'import os\nimport pathlib\n\nTIMEOUT = 10\nLEGACY_MODE = True\n' > app.py`).
2. "In app.py add `import logging` after `import pathlib`, set TIMEOUT to 30 and remove
   LEGACY_MODE, in one change." Expect one `file_edit` call with three edits, one diff, and
   an `expected_sha256` taken from the read.
3. `/changes` shows one entry; `/undo` restores the original.
4. "Show me the diff for changing TIMEOUT to 60 in app.py without changing it." Expect a
   dry run; the file is unchanged.
5. On a file with 200+ lines, ask to delete 150 of them: expect the approval prompt; answer
   `n` and check the file is unchanged.
6. "Create notes/todo.md with a short checklist." Expect `file_create` (with
   `create_parents`); `/undo` removes the file and the folder.
7. "Set TIMEOUT to 45 in app.py and add tests/test_app.py with a test for it." Expect
   `coder_agent` -> `change_set_apply` with two files, then `code_check`; `/undo` takes both back.
8. Stale-edit check (also covered by the tests; a mid-turn change is hard to time by hand):
   ask "show me app.py", then edit `app.py` yourself in another terminal, then ask for a
   change. Expect the agent to read the file again (new SHA-256) before editing; if it used
   the old hash, expect "changed since it was read" and no change.
9. Add `coder: {checks: {tests: [<your venv>/bin/python, -m, pytest, -q]}}` to the config,
   restart, ask "run the tests": expect the approval prompt with the command; answer `y`.
