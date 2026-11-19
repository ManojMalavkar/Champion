# Step 4d - Flexible editing

Status: 4d.1 and 4d.2 built (224 tests). 4d.3-4d.7 follow (see PLAN.md, Step 4d).

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
* `expected_sha256` (optional until 4d.4) refuses the edit if the file changed since it
  was read. Every result gives the new SHA-256 (line `File SHA-256 now ...`, before the diff).

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

### Local check on the Linux machine

```
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion
git pull && python -m pytest -q          # expect 224 passed
cd /d/people/t0018k6/champ-test/work && champion chat --level 1.2
```

1. Create a small file first (for example `printf 'import os\nimport pathlib\n\nTIMEOUT = 10\nLEGACY_MODE = True\n' > app.py`).
2. "In app.py add `import logging` after `import pathlib`, set TIMEOUT to 30 and remove
   LEGACY_MODE, in one change." Expect one `file_edit` call with three edits, one diff.
3. `/changes` shows one entry; `/undo` restores the original.
4. "Show me the diff for changing TIMEOUT to 60 in app.py without changing it." Expect a
   dry run; the file is unchanged.
5. On a file with 200+ lines, ask to delete 150 of them: expect the approval prompt; answer
   `n` and check the file is unchanged.
