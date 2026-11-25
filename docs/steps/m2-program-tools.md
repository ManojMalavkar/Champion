# Step M2 - Program tools (trusted external programs)

M2 lets an agent run an external program or script as a checked tool: declared once, trusted once by the user, refused if anything changes. It generalises step 4c (shell scripts) to any program, and is the base for T2 (HPC: `mjob`, queue tools), T3 (ANSA/META), T4 (PRIMER) and T6. The HPC agent itself is not on `main`; it is built on its track.

## Decisions (user, 24-25 Nov 2026)

1. **Track programs must be trusted.** A program a track declares in code (`register_program`) follows the same rules as a manifest program: the user runs `champion programs trust NAME`.
2. **Remote is its own permission, separate from local edit access.** Every `remote` run asks the user. Local edit access is needed only for the paths a program may change (path parameters with `access: edit`); a remote program without such paths runs at a read level (1.1). `edit` and `delete` programs must declare the paths they change.
3. **Every interpreter is hashed**, including system ones in `/usr/bin` and ones found through PATH (`#!/usr/bin/env NAME`, looked up in `path_prepend` then `/usr/bin`, `/bin`). Trust stores the requested path (as declared, on the `#!` line or found on PATH), the resolved file (links followed) and its SHA-256. Any change, including an OS update of `/usr/bin/dash`, means retrust.
4. **Direct-tool threshold 15, configurable** (`programs.max_direct_tools`). Above it an agent gets `programs_list` and `program_run(name, arguments)`.
5. **Module environments** are written as fixed values: `env_set` (fixed variables), `env_pass` (names copied from the user's environment) and `path_prepend` (folders before `/usr/bin:/bin`). Nothing runs through a shell, so `module load` (a shell function) cannot run; the user writes its result into the entry.

Also confirmed:
* The trusted entry digest (`ProgramSpec.fingerprint`) covers every field: program path, class, agent, description, typed parameters, argument template, working folder, environment (`env_pass`, `env_set`, `path_prepend`), interpreter, timeout and limits, approval policy and source. A test changes each field and checks the digest changes.
* **Duplicate names fail**, never override: two manifest entries with one name, or a manifest and a track entry with one name, refuse every entry with that name; `register_program` raises for a second, different declaration; the manifest is read with duplicate YAML keys refused (a second `class:` cannot hide the first).
* **No undo for program changes until M3.** What a program changes is audited (one `/changes` entry per edit path, audit row with program, hash, command, exit code) but cannot be undone: Champion cannot know what changed inside a path. Every result, approval prompt, tool description and `champion programs trust` output says so. Undo needs the staging of M3.

## Files

| File | What |
| --- | --- |
| `champion/programs/spec.py` | `ProgramSpec`, `Param`, `parse_entry` (all rules, clear errors), `register_program` for tracks |
| `champion/programs/manifest.py` | folder and file rules, interpreter resolution, strict YAML, trust store, status of every program, `trust` / `untrust` |
| `champion/programs/runner.py` | `run_program`: arguments, paths, access, approval, hash check, run, change list |
| `champion/programs/tools.py` | `program_<name>` tools or `programs_list` / `program_run`; added to the named agents at chat start |
| `champion/core/config.py` | `programs` section: `folder`, `trust_file`, `max_direct_tools` (15), `max_timeout_seconds` (3600) |
| `champion/execution/commands.py` | `run_steps(executables=..., path_prepend=...)`: run a trusted program by absolute path |
| `champion/cli.py`, `champion/doctor.py` | `champion programs list|check|trust|untrust`; a programs block in `champion doctor` (WARN, never FAIL) |
| `examples/programs/` | `programs.yaml`, `run_summary.sh` (read), `clean_run_dir.sh` (delete) |
| `tests/test_programs.py` | 74 tests |

## A manifest entry

```yaml
programs:
  - name: clean_run_dir
    program: clean_run_dir.sh          # in programs.folder; or absolute; or config:<section>.<key>
    description: Delete the result files of one LS-DYNA run folder
    agent: linux_coder
    class: delete                      # read | edit | delete | remote
    args: ["{run_dir}"]                # literal tokens or exactly {param}
    params:
      run_dir: {type: path, access: edit, required: true}
    timeout_seconds: 120
    # cwd: "{run_dir}"   env_set: {LSTC_LICENSE_SERVER: "31010@lic"}   env_pass: [LM_LICENSE_FILE]
    # path_prepend: [/opt/tools/bin]   interpreter: /opt/venv/bin/python   approval: always
```

Parameter types: `path` (read or edit access, checked exactly like the file tools), `int` / `float` (min, max), `bool` (flag), `enum`, `string` (pattern, at most 200 characters). Values that start with `-` are refused unless they come from an enum.

## The order of checks for one run

1. Arguments typed and checked; every path through `check_path` with its declared access.
2. Access: edit level only if the program takes edit paths.
3. Status: trusted now (program file, interpreter requested / resolved / SHA-256, entry digest).
4. Approval for `delete`, `remote`, `approval: always`: shows command, interpreter, folder, paths, program SHA-256 and the no-undo warning; the approval fingerprint includes the hash and the command.
5. All hashes taken again just before the start; any change refuses the run.
6. Run without a shell: clean environment plus declared names, `path_prepend`, time / CPU / memory / file limits, process-group kill, output cap.
7. Change list and audit row.

## Checks (Databricks serverless, copy of `main` + M2, 25 Nov 2026)

* `pytest -q`: **453 passed** (379 before M2, chromadb installed so the 9 knowledge tests run; 74 new).
* Trust, race and path tests: interpreter link retargeted to an identical copy -> `changed (interpreter target)`; interpreter changed during the approval prompt -> not run; program replaced by a link after trust -> refused; program path leaving the folder or through a linked folder -> refused; interpreter inside the working folder -> refused; group-writable trust file -> nothing trusted; remote at 1.1 runs after approval, remote with an edit path at 1.1 refused.
* End to end with `examples/programs/`: `champion programs list` (untrusted), `trust run_summary`, `trust clean_run_dir`, `check` (trusted); `program_run_summary` on a fake run folder (5 files, 2 d3plot, normal termination); `program_clean_run_dir` asked for approval with the no-undo warning, removed the 3 result files, kept `main.k` and `messag`, one `deleted` entry in the change list.

## Linux check finding (25 Nov 2026)

`champion programs list` refused the default folder: `programs folder /home/<user>/.champion/programs: /home is a symbolic link`. On that machine `/home` is a link, and `~` was expanded with the plain `HOME`. Fix: `programs.folder` and `programs.trust_file` expand `~` to the real home (links resolved), as the memory paths have done since 4e.8. A path written by hand through a link is still refused, and the error now names the real path to use. Test `test_home_behind_a_link_uses_the_real_home` fails on `ca0d8f3` and passes with the fix; 454 tests pass.

## Verify (Linux, after pull)

```bash
cd <main worktree>
git pull
python -m pytest -q            # 454 passed with chromadb installed (445 passed, 9 skipped without)
mkdir -p ~/.champion/programs && chmod 700 ~/.champion/programs
cp examples/programs/* ~/.champion/programs/ && chmod 755 ~/.champion/programs/*.sh
champion programs list         # two untrusted programs
champion programs trust run_summary
champion programs trust clean_run_dir
champion chat --level 1.2      # start line lists program_run_summary, program_clean_run_dir
```

Then in the chat: "Summarise the run in runs/a" -> `program_run_summary`; "Clean the result files in runs/a" -> approval prompt, then `/changes`. Edit a script and restart: the start line says it changed and the tool is not offered.
