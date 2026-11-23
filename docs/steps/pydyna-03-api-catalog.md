# PyDyna step 03 - API catalog and `lookup_api` (T1.2a, roadmap PD-04)

## What it does
Exact records of the PyDyna (ansys-dyna-core) API, read from the package installed in the
Python that runs Champion, with package version and source file. It answers "does this
name exist in the installed version, and how is it called" without the vector database.

* `champion/specialists/pydyna/api_catalog.py`
  * Finds the package through `importlib.metadata` (`ansys-dyna-core`: folder and version).
    The package files are parsed with `ast`; nothing of the package is imported or run.
  * Keyword classes (`keywords/keyword_classes/auto` and `manual`): a fast text scan at
    start (class name, `keyword`, `subkeyword`); a file is parsed only when one of its
    keywords is looked up. A keyword record has every card in order (plain cards, option
    cards such as TITLE, cards of a card-set helper class, tables, series) and every field:
    Python attribute name, type, position, width, default, description (from the field's
    property docstring), plus the options and curve links. When `manual` and `auto` have
    the same class, the manual one is used (the package exports it last) and the other is
    named in `other_classes`.
  * The rest of the package (`Deck`, `lib`, `run`, ...): public classes with their methods
    (signature, parameters, required or not, types, defaults, return type, docstring),
    properties (type, settable) and public module functions; `from ansys.dyna.core import
    Deck` when the package exports the name.
  * Names: `Deck`, `Deck.import_file`, `ansys.dyna.core.lib.deck.Deck`, `Mat024`,
    `keywords.Mat024`, `*MAT_024`, `MAT_024`, `mat_024`, `*MAT_024_TITLE` (keyword with an
    option). Unknown names come back with close names (`Mat24` -> `Mat024`, `*MAT_24` ->
    `*MAT_024`, `Deck.imprt_file` -> `Deck.import_file`); an ambiguous class name lists the
    full names.
  * Built once per package folder and version (cached).
* Tool `lookup_api` (right `none`, policy entry like the other PyDyna tools), given to
  the Keyword and the Code sub-agents. The Code sub-agent must confirm every class,
  method, property and keyword class with it; the Keyword sub-agent uses it for the exact
  cards and types, `lookup_keyword` stays for the curated units and limits.
* A missing package is a tool error: "ansys-dyna-core is not installed in the Python that
  runs Champion (pip install ansys-dyna-core)".

Why: the keyword schema of the knowledge base is incomplete (for `*MAT_024` only the 8
fields of card 1, all typed `float`); the installed package has all 30 fields over 4 cards
plus TITLE, with the right types. Roadmap PD-04 asks for the installed package as the
first authority.

## Measured on the real package (ansys-dyna-core 0.12.1 wheel, Databricks serverless)
* Start: 0.94 s; 3156 keyword classes, 3150 keyword names, 71 classes, 276 members, 64
  functions, 0 files skipped.
* All 3156 keyword files parse (11 s for all; one lookup 1-5 ms); none has 0 cards.
  Cards found: 12593 plain, 58 table, 32 series, 12 table group, 2 text, 2 include;
  67701 fields, 1968 without a property description.
* `*MAT_024`: cards of 8, 5, 8, 8 fields and TITLE (1 field); `mid` is `int`; links
  `lcss` DEFINE_CURVE_OR_TABLE, `lcsr` DEFINE_CURVE. `*MAT_PIECEWISE_LINEAR_PLASTICITY`
  is its own class. `*SECTION_SHELL`: 8 cards through the `SectionShellCardSet` helper.
* `Deck`: 37 members; `Deck.import_file(path: str, encoding: str = 'utf-8')`.

## Tests
`tests/pydyna/test_pydyna_api_catalog.py`, 26 tests: a fake ansys-dyna-core in `tmp_path`
with the real layout (FieldSchema tuples, OptionCardSet, CardSet helper, TableCard,
SeriesCard, manual over auto, a broken keyword file, a broken lib file, a private module);
every fake module raises at import, so the tests fail if the catalog imports it. The last
test reads the real installed package (skipped when it is not installed). The group test
in `test_pydyna_tools.py` now expects `lookup_api` in both sub-agents.

Serverless (copy of the track, ansys-dyna-core 0.12.1 installed without dependencies):
full suite 346 passed, 6 skipped (5 knowledge-data tests, 1 chromadb test); the real-package
test passed. Breaking the option cards, the card-set expansion or the manual/auto order
makes 1 test fail each.

## Verify (Linux, after `git pull`)
```bash
python -m pytest -q tests/pydyna            # the real-package test must pass, not skip
python -m pytest -q                         # full suite
python - <<'EOF'
import json, time
from champion.specialists.pydyna import api_catalog as ac
start = time.monotonic()
c = ac.catalog_for()
print(f"start {time.monotonic() - start:.2f} s", c.stats())
for name in ["Deck", "Deck.import_file", "*MAT_024", "*MAT_024_TITLE", "*MAT_PIECEWISE_LINEAR_PLASTICITY",
             "keywords.SectionShell", "Mat24", "Deck.imprt_file"]:
    try:
        r = c.lookup(name)
    except ac.CatalogError as exc:
        print(f"{name}: ERROR {exc}")
        continue
    if r["kind"] == "keyword":
        print(f"{name}: {r['keyword']} {r['class']} {r['source_path']} cards "
              f"{[(x['card'], x['kind'], x.get('option'), len(x['fields'])) for x in r['cards']]}")
    elif r["kind"] == "class":
        print(f"{name}: {r['import']} ({len(r['members'])} members) {r['source_path']}:{r['line']}")
    else:
        print(f"{name}: {r['signature']} {r['source_path']}:{r['line']}")
print(json.dumps(c.lookup("*MAT_024")["cards"][0]["fields"][:2], indent=1))
EOF
```
Expected: version 0.12.1 and about the counts above; `*MAT_024` with cards of 8, 5, 8, 8
fields and TITLE; `Mat24` and `Deck.imprt_file` are errors with close names `Mat024` and
`Deck.import_file`; `mid` is `int` with the description "Material identification...".

Live check in the chat (optional): `champion chat`, ask "which fields does *MAT_024 have in
PyDyna, with types and defaults" and "how do I read a keyword file with PyDyna"; the tool
lines must show `PyDyna/Keyword` or `PyDyna/Code` calling `lookup_api`.
