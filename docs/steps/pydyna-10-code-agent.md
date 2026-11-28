# PyDyna 10 - T1.2 PyDyna Code: checked templates, scripts written and checked, never run

Track T1, branch `track/pydyna` (merged `main` `c7cbea5` first, as `582fc8f`). Built 28 Nov 2026.

## User decisions (28 Nov 2026)

1. Use the core `file_create`, `file_write` and `code_check` tools; no PyDyna write tool.
2. Two checked templates now: deck skeleton; material, section and part. Contact, boundary
   condition, initial velocity, load curve and prescribed motion templates move to T1.6 skills.
3. Check the code text with `check_pydyna_script` before writing; then `code_check` and
   `check_pydyna_script` on the written file. Exact edits for fixes; stop after three
   unsuccessful correction attempts.
4. Templates use the offline keywords/Deck API, not DynaMech or another server-dependent pre API.
5. T1.2 creates and checks scripts only; it never runs them.

Follow-up (user, 28 Nov 2026, after the first build):

6. Keep the templates immutable; record their provenance and package compatibility.
7. Enforce authorized `.py` output paths.
8. Enforce the attempt limit in code: count only failed write/edit-and-validation cycles, stop
   after three, stop earlier if the code or the validation result does not change.
9. Confirm the Code agent has no execution, subprocess or M2 program tools.
10. Commit, but push only after the Linux and live level-1.2 checks (with `/changes` and `/undo`).

## What changed

| File | Change |
| --- | --- |
| `champion/specialists/pydyna/templates.py` (new) | `deck_skeleton` and `material_section_part` as text (never imported or run by Champion); `names()`, `get()`, `listing()`; the other five name T1.6 |
| `champion/specialists/pydyna/tools.py` | tool `pydyna_template` (right `none`, reads no file); Code tools: `pydyna_template`, `lookup_api`, `search_pydyna_api`, `lookup_keyword`, `check_pydyna_script` + core `file_create`, `file_edit`, `file_write`, `code_check` |
| `champion/specialists/pydyna/__init__.py` | Code instructions: offline API only (never DynaMech / `ansys.dyna.core.pre`), template first, the five writing steps (check text, `file_create`, `code_check` + `check_pydyna_script` on the file, exact `file_edit` fixes, stop after three), never run; descriptions updated |
| `champion/specialists/pydyna/api_catalog.py` | two catalog fixes (Findings 2 and 3) |
| `champion/specialists/pydyna/guard.py` (new) | wraps the three core write tools and the two validators for the Code sub-agent only (same names and arguments): output path rules and the code-enforced correction limit (below) |
| `tests/pydyna/test_pydyna_templates.py` (new) | 20 tests (Tests) |
| `tests/pydyna/test_pydyna_guard.py` (new) | 22 tests (Tests) |
| `tests/pydyna/test_pydyna_tools.py` | Code tool list; core tools keep their own rights; only Code has file tools |

### Guard (decisions 7 and 8, `guard.py`)

The core tools still do all the work (access level, path policy, approval, `/changes`, `/undo`,
audit); the wrappers only refuse or count.

* Output paths: a write or edit is `BLOCKED` (audit row `deny`, reason `pydyna guard: ...`)
  unless the file ends in `.py`, its name is letters, digits, `_`, `-` (starting with a letter or
  digit), it would not be imported instead of a module when a script in that folder runs
  (Python's standard modules, `ansys`, `pandas`, `numpy`, `champion`, `conftest`, `setup`,
  `sitecustomize`, `usercustomize`), and it is not inside Champion's own package (templates and
  guard included). Whether the folder may be written stays the core path policy's decision.
* Correction limit, per chat session and file: a cycle is a write or edit of the file followed
  by its validation (`check_pydyna_script` with `path`, `code_check`); it fails when a validation
  of the file reports a problem (a second failing validator in the same cycle counts once).
  The first write is the original, not a correction. After three failed correction cycles the
  validation result says `STOP` (`correction_attempts`) and every further write or edit of that
  file is `BLOCKED` for the session. It stops earlier when there is no progress: the code is
  back to an earlier failed version (same SHA-256), or a validator reports the same problems as
  in the previous failed cycle. Checks of code text, searches and lookups are not counted. A
  cycle in which both validators pass resets the count.
* Limits: a different file name starts its own count (the instructions forbid switching names);
  a new chat starts at zero.

### Templates: immutable, with provenance (decision 6)

`Template` is frozen, `TEMPLATES` a read-only mapping. Each template records the SHA-256 of its
code; `get` refuses a template whose code no longer matches (a change must update the hash and
be checked again). Recorded with each template: `provenance` (written for T1.2 from the
`lookup_api` records of ansys-dyna-core, not from DynaMech or server examples; decided by the
user), `api_names` (every PyDyna name it uses, each tested to be in the installed catalog),
`checked_with` (`0.12.1`). `pydyna_template` returns these with `compatibility`: "checked with
the installed ansys-dyna-core 0.12.1", or "not checked with this version; check_pydyna_script on
the text decides".

`file_edit` is a core tool too: it makes the exact edits of decision 3 (`old_text` -> `new_text`,
with `expected_sha256` from the last change). `file_write` replaces a whole file and asks first;
the instructions allow it only for a file the user names.

The Code sub-agent has no tool that runs anything (decision 9): every tool has right `none`,
`read` or `edit` (tested), no `shell_run`, `shell_pipeline`, `run_check` or program tool, and
none of its tools starts a process (`subprocess` is used only by `source_data.py`, the `git
clone` command-line script, which is no agent tool). `code_check` compiles, `check_pydyna_script`
reads the AST; neither imports or runs the script. A template writes `<script>.k` next to itself
only when the user runs it.

Limitation (M2 program tools, found 28 Nov 2026): `attach_program_tools` (core, M2) adds a
trusted program to any agent its manifest entry names. Checked: a trusted entry with
`agent: pydyna_code` is attached as `program_echo_args` without a problem line. Today the Code
agent has none only because no entry names it (tested with the default, empty program
folder). The track cannot refuse it without a core change; the Code spec declares
`extra={"programs": False}` as a placeholder. CR-5 on `main` (approved by the user, 28 Nov 2026)
replaces it with `accepts_program_tools=False`; it is **mandatory before T1.4** (running
scripts) and is not implemented in this track (shared M2/core policy). See Open before T1.4.

## Templates

| Template | Keywords | Imports |
| --- | --- | --- |
| `deck_skeleton` | `*TITLE` (Deck title), `*CONTROL_TERMINATION` (ENDTIM 0.01), `*CONTROL_TIMESTEP` (TSSFAC 0.9), `*DATABASE_GLSTAT`, `*DATABASE_MATSUM` (1e-5), `*DATABASE_BINARY_D3PLOT` (5e-4) | `pathlib`, `ansys.dyna.core` `Deck`, `keywords` |
| `material_section_part` | `*MAT_024` (steel: RO 7.85e-9, E 210000, PR 0.3, SIGY 355, ETAN 1000), `*SECTION_SHELL` (ELFORM 2, NIP 5, 1.5 mm), `*PART` (PID 1, SECID 1, MID 1) | `pathlib`, `pandas`, `ansys.dyna.core` `Deck`, `keywords` |

Units mm, t, s, N, MPa (stated in each template). No nodes or elements: the deck with one shell
part has the part definition; geometry is the user's (or a later template).

## Findings

1. `ansys.dyna.core.pre` (DynaMech) is not in ansys-dyna-core 0.12.1 at all (import fails). The
   curated docs record in `pydyna_api` that uses `DynaMech.create_section_shell` is for another
   version; the instructions forbid it even when a search shows it.
2. `check_pydyna_script` gave a false `unknown-field` warning for table data given to the
   constructor (`Part(parts=df)`, `Node(nodes=df)`): PyDyna passes the table name of
   `TableCard` / `TableCardGroup` positionally (4th argument), the catalog read only `name=`.
   Checked by running: `Part(parts=df)` writes the part row, `Node(nodes=df)` both nodes. Fixed;
   a wrong name (`partz=`) is still a warning. Without the fix the template could not pass the
   rule "fix every warning".
3. `lookup_api("*PART")` said "not in ansys-dyna-core 0.12.1": the catalog joined keyword and
   subkeyword also when they are equal (`*PART_PART`). PyDyna's `KeywordBase._get_base_title`
   writes the keyword alone then. 9 keywords: `*CASE`, `*COMMENT`, `*FATIGUE`, `*HOURGLASS`,
   `*INCLUDE`, `*KEYWORD`, `*NODE`, `*PARAMETER`, `*PART`. Fixed; for all 3171 keyword classes
   the catalog name now equals PyDyna's `get_title()` (0 differences, 7 classes skipped that
   need constructor arguments).
4. At level 1.1 the model is not offered `file_create`, `file_edit`, `file_write` (the core
   tools' own `requires` edit), so it cannot even try a write; `code_check` and the PyDyna
   tools stay. At 1.2 they are offered. The instructions say to give the checked code in the
   answer when no write tool is there.
5. The limit of three correction attempts was first in the instructions only; it is now
   enforced in code (`guard.py`, decision 8), without a core change.
6. openai-agents 0.26.0 (serverless) wraps `on_invoke_tool` in its own invoker; the guard
   replaces it with `dataclasses.replace` and calls the core tool's invoker, so it works with
   the SDK's tool context. The tests drive the tools through the scripted chat, not directly.

## Tests (Databricks serverless, ansys-dyna-core 0.12.1 installed)

* `tests/pydyna/test_pydyna_guard.py`: 22 passed, none needs the package.
  * Path rules: 12 refused names (`deck.k`, `notes.txt`, `build deck.py`, `.hidden.py`, `a.b.py`,
    `pandas.py`, `json.py`, `setup.py`, `conftest.py`, `ansys.py`, `sitecustomize.py`,
    `__init__.py`), 4 accepted, a file in Champion's package refused.
  * Counter: stop after three failed corrections (first write not counted); same problems
    after a correction stops at once; code back to an earlier failed version stops; a second
    failing validator in one cycle counts once; both passing resets; code text and checks
    without a write are not counted.
  * Scripted chat: four broken versions of `x.py` -> notes "first version", "correction 1 of 3",
    "correction 2 of 3", "STOP: ... 3 correction attempts failed"; the next `file_edit` is
    `BLOCKED: STOP`, the file is unchanged, one audit `deny` row. `pandas.py`, `deck.k` and a
    file in Champion's package are refused (3 audit rows), `build_deck.py` is written. A
    written script is listed by `/changes` and removed by `/undo`.
  * Nothing runs: no program, shell or check-running tool; the five file and check tools are
    the guarded copies, not the core objects (same argument schema).
* `tests/pydyna/test_pydyna_templates.py`: 20 passed. Without the package 12 are skipped (API
  check, recorded API names, the two catalog fixes, the chat flows).
  * Immutable: assigning to `TEMPLATES` raises `TypeError`, to a template
    `FrozenInstanceError`; recorded SHA-256 equals the code; a changed template is refused by
    `get` and by the tool. Provenance, `checked_with`, compatibility for 0.12.1, 0.13.0 and
    "not installed".
  * Templates: two names, others name T1.6; each compiles, imports only `pathlib`, `pandas`,
    `ansys.dyna.core` (`Deck`, `keywords`), no `DynaMech` / `.pre`, writes `<script>.k`;
    each passes `check_pydyna_script` with 0 errors and 0 warnings.
  * Tool `pydyna_template`: list, one template, unknown name points to T1.6; right `none`.
  * Code sub-agent: core file tools, nothing that runs, the rules in its instructions.
  * Catalog: `Node(nodes=)` and `Part(parts=)` no issue, `Part(partz=)` warning;
    `*PART`, `*NODE`, `*HOURGLASS` found, `*PART_PART` not.
  * Scripted chat (orchestrator -> `pydyna_agent` -> `pydyna_code_agent`): at 1.2 template ->
    check text -> `file_create` -> `code_check` -> check file; the file has the template text,
    both checks ok, one change in `/changes`, no `.k` in the working folder (never run). A
    wrong field written (`etann`) is found in the file and fixed with one exact `file_edit`.
    At 1.1 and 1.2 the offered tools as in Finding 4.
* Mutation checks (each undone): table-name fix off 4 failed; `*PART` fix off 4 failed; `etann`
  in a template 3 failed; `file_create` not in the Code tools 4 failed; "three" rule removed 1
  failed. Guard: limit 3 -> 99 2 failed; same-problems check off 1; same-code check off 1; every
  path accepted 14; unguarded `file_edit` for Code 2; template SHA-256 check off 1.
* Full suite: 662 passed, 13 skipped (15 real-package tests that were skipped before now run here
  and pass).
* My own check only (not part of T1.2, which never runs scripts): both templates run in a
  scratch folder wrote `deck_skeleton.k` (`*KEYWORD`, `*TITLE`, `*CONTROL_TERMINATION`,
  `*CONTROL_TIMESTEP`, `*DATABASE_GLSTAT`, `*DATABASE_MATSUM`, `*DATABASE_BINARY_D3PLOT`,
  `*END`) and `material_section_part.k` (`*MAT_024`, `*SECTION_SHELL`, `*PART` row 1 1 1).

## Acceptance checks on Linux (user, after the commit, before the push)

The commit is not pushed yet (decision 10).

| Item | Value |
| --- | --- |
| T1.2 commit | `bbaa7a0` (`bbaa7a060073099c983196aa8687e734fc300433`), ManojMalavkar, local branch `t1.2-review` |
| Base merge | `582fc8f` (merge of `main` `c7cbea5`; already on `origin/track/pydyna`) |
| Bundle | `/Workspace/Users/t0018k6@inetpsa.com/Champion-t1.2.bundle` (28764 bytes; ref `refs/heads/t1.2-review` -> `bbaa7a0`; requires `582fc8f`) |
| Bundle SHA-256 | `89552fd704279cad84b36e3402bdbe56d9953606ec05d9df2c710a328c22698d` (also in `Champion-t1.2.bundle.sha256` next to it) |
| Bundle checked (28 Nov 2026) | `git bundle verify`: okay; `sha256sum -c`: OK; a fresh clone at `582fc8f` fast-forwards to `bbaa7a0`, same tree `44e801b` |
| Linux tests | **pending** (user) |
| Live level-1.2 chat with `/changes` and `/undo` | **pending** (user) |

After both pass, the same commit is pushed unchanged: `git push origin bbaa7a0:refs/heads/track/pydyna`
(fast-forward from `582fc8f`). No `review/t1.2` branch is created.

The commit comes as a Git bundle,
`/Workspace/Users/t0018k6@inetpsa.com/Champion-t1.2.bundle` (download it from the workspace
browser, or `databricks workspace export /Users/t0018k6@inetpsa.com/Champion-t1.2.bundle --file
~/Champion-t1.2.bundle --format AUTO`). Fast-forwarding to it gives the same commit that is
pushed after the checks, so the later `git pull` has nothing new. The bundle needs `582fc8f`
(the pushed merge of `main`) first: hence `git pull --ff-only`.

One paste (venv, bundle, tests, both templates in a disposable folder); set `BUNDLE` first:

```bash
BUNDLE=~/Champion-t1.2.bundle && \
source ~/.ansa_meta_copilot/.venv/bin/activate && \
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna && \
echo "89552fd704279cad84b36e3402bdbe56d9953606ec05d9df2c710a328c22698d  $BUNDLE" | sha256sum -c - && \
git pull --ff-only && git bundle verify "$BUNDLE" && \
git fetch "$BUNDLE" t1.2-review && git merge --ff-only FETCH_HEAD && \
git log --oneline -1 && \
python -m pytest -q 2>&1 | tail -1 && \
REPO=$PWD && T=$(mktemp -d /tmp/pydyna-t12-XXXX) && echo "disposable folder: $T" && \
python - "$T" <<'EOF'
import sys
from pathlib import Path
from champion.specialists.pydyna import templates as tpl
for name in tpl.names():
    Path(sys.argv[1], f"{name}.py").write_text(tpl.get(name).code, encoding="utf-8")
    print("template written:", name)
EOF
cd "$T" && for f in deck_skeleton material_section_part; do python $f.py || echo "FAIL $f"; done && \
ls -l "$T" && grep -c '^\*' *.k && grep '^\*' material_section_part.k && cd "$REPO"
```

Expected: `...: OK` for the SHA-256; `git log` shows `bbaa7a0`; `pytest` all passed, 0 skipped;
`wrote .../deck_skeleton.k` and
`wrote .../material_section_part.k`; 8 and 6 lines starting with `*`; `*KEYWORD`, `*TITLE`,
`*MAT_024`, `*SECTION_SHELL`, `*PART`, `*END`.

Live chat at level 1.2 in another disposable folder (the working folder is the folder Champion
starts in; the banner shows it):

```bash
source ~/.ansa_meta_copilot/.venv/bin/activate && \
REPO=/nobackup/people/t0018k6/script/Github_repo/LLM/Champion-pydyna && C=$(mktemp -d /tmp/pydyna-chat-XXXX) && \
cd "$C" && CHAMPION_CONFIG=$REPO/config/config.yaml PYTHONPATH=$REPO \
python -c "import sys; from champion.cli import main; sys.exit(main())" chat --level 1.2
```

In the chat:

1. `Write a PyDyna script shell_part.py that builds a deck with one shell part: steel *MAT_024,
   1.5 mm shells. Do not run it.`
2. `/changes`. In a second terminal: `ls $C` (only `shell_part.py`) and optionally
   `head -30 $C/shell_part.py`.
3. `/undo`, then `/changes` again.
4. Optional, the path guard: `Save the same script as pandas.py.`
5. `/exit`, then `ls $C`.

Expected:

* 1: tool lines `pydyna_template`, `check_pydyna_script` (code), `file_create`, `code_check`,
  `check_pydyna_script` (path); no DynaMech, no `shell_run` / `run_check` / `program_` line;
  "Changed files this turn: 1"; the answer gives the path and both check results and says it
  was not run.
* 2: `shell_part.py` created; `ls` shows no `.k` (nothing ran).
* 3: `/undo` removes `shell_part.py`; the second `/changes` shows it undone.
* 4: `file_create` -> `BLOCKED: pandas.py would be imported instead of the module pandas`.
* 5: `ls` is empty (or has no `pandas.py`).

Not expected in a short chat: a `STOP`. The tests force it with broken versions (Tests).

Report back: the one-paste output (last lines), and for the chat the tool lines and the
`/changes` / `/undo` output. Push only after that.

## Open before T1.4 (mandatory)

* CR-5 on `main`, approved by the user (28 Nov 2026), mandatory before T1.4; a shared M2/core
  policy change, so not implemented in this track:
  * agents can declare `accepts_program_tools=False` (a `SpecialistSpec` field);
  * M2 (`attach_program_tools`) never attaches trusted program tools to such an agent;
  * `pydyna_code` uses this restriction (replacing the `extra={"programs": False}` placeholder);
  * a track or a program manifest cannot override it;
  * fail-closed tests (a trusted entry for such an agent is not attached, also when the flag
    is missing or malformed in a way that would otherwise allow it);
  * a skipped attachment is reported by `champion programs check` or `champion doctor`.

## Acceptance results (user, Linux, 28 Nov 2026)

The user changed the order: `bbaa7a0` was pushed unchanged to `track/pydyna` (`582fc8f..bbaa7a0`)
before the checks; Linux pulled it (fast-forward from `0fc779c`).

* Tests: `675 passed` in 130 s, 0 skipped.
* Templates: written with SHA-256 `3850ebf65a6b` / `bd773b80249a` (as recorded); run by hand
  they wrote `deck_skeleton.k` (8 keyword lines) and `material_section_part.k` (6: `*KEYWORD`,
  `*TITLE`, `*MAT_024`, `*SECTION_SHELL`, `*PART`, `*END`). Passed.
* Chat start: failed first with "CHAMPION_CONFIG points to a missing file": the untracked link
  `config/config.yaml -> ../Champion/config/config.yaml` (21 Nov) was broken (relative to
  `config/`, one `..` short). Fixed by the user: `../../Champion/config/config.yaml`. Not a T1.2
  problem.
* Live chat (`atlas/gpt-oss-120b`, level 1.2): **failed**. The orchestrator wrote a script
  itself (`dyn.Model()`, `add_material` ...: an API not in ansys-dyna-core) and sent "create
  shell_part.py with this content" to `linux_coder_agent`, which wrote it with its core
  `file_create`. PyDyna, the templates, both checks and the guard were never called. "Save the
  same script as pandas.py" also went to Linux and was written (the guard covers only the Code
  sub-agent). `/changes` and `/undo` worked (`#2 undone`, `[undone by #2]`).

Cause: the PyDyna description began with "knowledge specialist" and named script writing
late; the Linux description says "create new text files"; no orchestrator rule forbids writing
a script itself and sending it as file content.

Fix in this track (T1.2.1, 28 Nov 2026, not committed): the PyDyna description now begins
"send it every request to write, save, change or check a PyDyna, PyDPF-Post or PyDPF-Core
script, with the user's goal and file name, never with code you wrote"; routing hints
"write a PyDyna script shell_part.py that builds a deck with one shell part" and "save the
PyDyna script as build_deck.py". Test: the orchestrator's PyDyna entry and hints (fails with
the old text). Serverless: 663 passed, 13 skipped. The routing itself can only be shown by a
live re-run.

Proposed for `main` (needs the user's OK; shared core): CR-6 orchestrator rule "never write
code or file content for a specialist; send the user's goal", so no agent receives code the
orchestrator invented. Open question: the Linux and Coder agents' `file_create` can still write a
`.py` that shadows a module (`pandas.py`); the path guard is only in the PyDyna Code sub-agent.

## Status

T1.2 `bbaa7a0` pushed 28 Nov 2026 (user decision). Linux tests and templates passed; the live
level-1.2 chat failed on routing (above). T1.2.1 routing fix built, not committed; then the
live chat again. CR-5 approved, mandatory before T1.4.
