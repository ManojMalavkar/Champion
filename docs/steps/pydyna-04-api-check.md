# PyDyna step 04 - API check of a script and `check_pydyna_script` (T1.2b, roadmap PD-05 PyDyna part)

## What it does
A static check of a PyDyna script against the installed ansys-dyna-core, through the API
catalog of T1.2a. The script is parsed with `ast`; nothing is run or imported. Only names
the script binds to `ansys.dyna.core` are checked; other libraries are not.

* `champion/specialists/pydyna/api_check.py` (new), `check_source(catalog, text, name)`:

  | Code | Severity | Found when | What happens when run (0.12.1) |
  | --- | --- | --- | --- |
  | `unknown-import` | error | `from ansys.dyna.core import Dek`, `import ansys.dyna.core.libx` | ImportError |
  | `unknown-name` | error | `keywords.Mat24` | AttributeError |
  | `unknown-member` | error | `deck.imprt_file(...)`, `mat.youngs` | AttributeError |
  | `missing-argument` | error | `deck.import_file()` | TypeError |
  | `too-many-arguments` | error | `deck.write(1, 2, 3, 4, 5, 6)` | TypeError |
  | `unknown-argument` | error | `deck.export_file("o.k", validat=True)` | TypeError |
  | `positional-argument` | error | `keywords.Mat024(1, 2)` | TypeError |
  | `read-only` | error | `deck.keywords = []`, `mat.cards = ...` | AttributeError |
  | `unknown-field` | warning | `keywords.Mat024(young=...)`, `mat.young = ...` | runs; the value is not in the deck |
  | `option-not-active` | warning | `keywords.Mat024(title="steel")`, TITLE never switched on | runs; the TITLE card is not written |
  | `unknown-option` | warning | `mat.activate_option("TITEL")` | runs; nothing is switched on |
  | `unknown-attribute` | warning | `deck.titel = "x"` | runs; adds a new Python attribute |
  | `replaces-method` | warning | `deck.append = None` | runs; the method is gone |
  | `syntax` | error | the script does not parse | SyntaxError |

  Every issue has line, column and, where useful, close names (`Mat24` -> `Mat024`,
  `imprt_file` -> `import_file`, `validat` -> `validate`). The option warning goes away
  when the script switches the option on for that object: `activate_option("TITLE")`,
  `options["TITLE"].active = True`, or setting the property (`mat.title = "steel"`).
  Constructor arguments that every keyword reads besides its fields (`format`,
  `user_comment`, ...) are accepted.
* Followed: every import form (`import ansys.dyna.core [as x]`, `from ... import Deck, keywords`,
  submodules, keyword files); variables made from a class or a keyword class
  (`deck = Deck()`, `mat = keywords.Mat024()`), in source order through the file. A
  variable bound to anything else (another value, a loop, `with`, a function argument) is
  no longer followed. When a class has a base outside the package (for example a pandas
  class), a missing member may come from it: nothing is reported for it.
* Tool `check_pydyna_script(path=..., code=...)`, Code sub-agent. Right `none`: script
  text needs no access. A path is read through the path policy with right `read`
  (`check_path`, as `code_check`): outside the allowed folders it is BLOCKED, symbolic
  links and sensitive files are refused; only `.py`, UTF-8, at most 1 MB. The Code
  sub-agent must check every script it gives and fix every error and warning; to review
  a user's script it checks the file and reports the issues with lines.

### Catalog changes for the check (`api_catalog.py`)
* For the check: `resolve(path)`, `module_members`, `class_members` (with bases, and
  whether every base is known), `keyword_api` (fields with their option, properties with
  settable, methods, attributes), `close_names`. Classes also list class attributes,
  nested classes and `self.x = ...` attributes of every method; modules list every
  top-level name (variables such as `__version__`, also in `try` / `if` blocks).
* Fixed lookups (also for `lookup_api`), found by comparing with the running package:
  * Alias subclasses with only their own subkeyword (`class Mat107(MatModifiedJohnsonCook):
    subkeyword = "107"`) were missing: 21 keyword names, among them `*SET_NODE`,
    `*MAT_107`, `*CONTROL_TIME_STEP`, `*MAT_HYPERELASTIC_RUBBER`. They now have the cards
    of their base class (`cards_from`).
  * For 6 keywords the lookup gave a legacy class the package does not export
    (`*SECTION_SOLID` -> `SectionSolidLegacy`, also `*SECTION_BEAM`, `*SECTION_TSHELL`,
    `*MAT_295`, `*INITIAL_STRESS_SHELL`, `*INITIAL_STRAIN_SHELL`). The class used is now
    the one `auto_keywords.py` / `manual_keywords.py` export, manual before auto.
  * Manual subclasses with the same name and no keyword of their own (manual
    `DefineTable(_AutoDefineTable)`) add their members (`linked_curves`).
* Real package: 3178 keyword classes, 3171 keyword names (was 3156 / 3150), 71 classes, 388
  members (was 276), 64 functions; start 0.90 s.

## Checked against the running package (ansys-dyna-core 0.12.1, Databricks serverless)
* Every keyword name: the class the catalog uses is the class `ansys.dyna.core.keywords`
  gives (3171 of 3171).
* All 3171 keyword classes built with no arguments: every public runtime name is a field or
  member in the catalog, every class is complete (no unknown base). All 71 classes: no
  class-level runtime name missing. So the check cannot report a name the package has.
* 20 scripts run for real and checked: every error case fails when run with the error
  the table gives; every warning case runs and loses the data as described; the correct
  scripts have no issue.
* The tests and examples of the PyDyna repository at tag v0.12.1 (60 files, 21976 lines, 1.8 s):
  no false error on imports or keyword classes. Found: 31 `unknown-field` (all checked:
  PyDyna ignores them, for example `kwd.Case(case_id=1, title=...)` writes an empty
  `caseid`, `InitialVelocityGeneration(sid=1)` has the field `id`); 1 `option-not-active`
  in the official Taylor bar example (`RigidwallPlanar(id=1)`: the ID card is not
  written); 5 `unknown-member` (`MeshBl.pid_link`, not in the installed 0.12.1 wheel);
  2 `replaces-method` (mocks in tests).

## Tests
`tests/pydyna/test_pydyna_api_check.py`, 35 tests: a fake ansys-dyna-core in `tmp_path`
(KeywordBase, Deck, a keyword with a TITLE option, an alias subclass, a manual override,
a legacy class that is not exported, a class with a base outside the package); every
fake module raises at import. Every issue code, every import form, the option switch in
three ways and three cases where the warning stays, variables that are no longer
followed, the tool with a file, with text, blocked paths, links, size, encoding. The real
package tests (skipped where it is not installed) run 12 scripts and compare the check
with what running them does, show the data loss of the warnings, and compare runtime names
of a sample of keyword classes with the catalog. The group test now expects
`check_pydyna_script` in the Code sub-agent.

Serverless (copy of the track, ansys-dyna-core 0.12.1): full suite 452 passed, 6 skipped
(5 knowledge-data tests, 1 chromadb test); all 35 new tests ran.

## Verify (Linux, after `git pull`)
```bash
python -m pytest -q tests/pydyna            # nothing skipped: the real-package tests must run
python -m pytest -q                         # full suite (expect 423 + 35 = 458 passed)
python - <<'EOF'
import json
from champion.specialists.pydyna import api_catalog as ac, api_check as ck
c = ac.catalog_for()
print(c.stats())
for name in ["*SET_NODE", "*MAT_107", "*SECTION_SOLID"]:
    r = c.lookup(name)
    print(name, r["class"], len(r["cards"]), r.get("cards_from"))
script = '''from ansys.dyna.core import Deck, keywords
deck = Deck()
mat = keywords.Mat24(mid=1)
steel = keywords.Mat024(mid=1, young=2.1e5, title="steel")
deck.imprt_file("x.k")
deck.keywords = []
'''
for issue in ck.check_source(c, script)["issues"]:
    print(issue["line"], issue["severity"], issue["code"], issue.get("close_names", ""))
EOF
```
Expected: `keyword_classes` 3178, `keyword_names` 3171; `*SET_NODE SetNode`, `*MAT_107
Mat107 ... MatModifiedJohnsonCook`, `*SECTION_SOLID SectionSolid`; issues: line 3
`unknown-name` (`Mat024` first), line 4 `unknown-field` and `option-not-active`, line 5
`unknown-member` (`import_file` first), line 6 `read-only`.
