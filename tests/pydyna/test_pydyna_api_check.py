"""T1.2b: PyDyna API check of a script (api_check.py) and the check_pydyna_script tool.

The tests build a small fake ansys-dyna-core in tmp_path (its modules stop at import, so a
test fails if the check ever imports one): KeywordBase, Deck, a keyword with a TITLE option,
an alias subclass with its own subkeyword, a manual override, a legacy class that is not
exported, and a class with a base outside the package. The last tests use the real installed
package (skipped where it is not installed): each wrong script is run and must fail, or must
run and lose data silently, as the check says.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
from agents import RunContextWrapper

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.policy.tool_policy import rule_for
from champion.specialists.pydyna import api_catalog as ac
from champion.specialists.pydyna import api_check as ck
from champion.specialists.pydyna import tools as pt
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError, run_guarded

STOP = 'raise SystemExit("package code ran")\n'

KEYWORD_BASE = '''
class KeywordBase:
    """Base of every keyword."""

    def __init__(self, **kwargs):
        self._format = kwargs.get("format")

    @property
    def options(self):
        """Options of the keyword."""

    @property
    def user_comment(self) -> str:
        """Comment."""

    @user_comment.setter
    def user_comment(self, value: str) -> None:
        pass

    def activate_option(self, option: str) -> None:
        """Switch an option on."""

    def write(self, buf=None, format=None) -> str:
        """Keyword text."""
'''

DECK = '''
import pandas

class Deck:
    """Container for keywords."""

    def __init__(self, title: str = None, **kwargs):
        self._title = title
        self.comment_header = None

    @property
    def title(self) -> str:
        """Title of the deck."""

    @title.setter
    def title(self, value: str) -> None:
        pass

    @property
    def keywords(self) -> list:
        """All keywords."""

    def append(self, keyword, check=False) -> None:
        """Add one keyword."""

    def import_file(self, path: str, encoding: str = "utf-8"):
        """Import a keyword file."""

    def write(self, filename=None, format=None) -> str:
        """Deck text."""

    def _connect(self):
        self.remote = True

    @staticmethod
    def from_text(text: str, *parts, strict: bool = False) -> "Deck":
        """Build a deck from text."""


class Frame(pandas.DataFrame):
    """A class with a base outside the package."""

    def own(self) -> None:
        pass


def load(path: str, *, validate: bool = False) -> Deck:
    """Load a deck."""


VERSION = "9"
'''

MAT_024 = '''
_MAT024_CARD0 = (
    FieldSchema("mid", int, 0, 10, None),
    FieldSchema("ro", float, 10, 10, None),
)

_MAT024_OPTION0_CARD0 = (
    FieldSchema("title", str, 0, 80, None),
)

class Mat024(KeywordBase):
    """DYNA MAT_024 keyword"""

    keyword = "MAT"
    subkeyword = "024"
    _option_spec_list = [
        OptionSpec("TITLE", "pre/1", 1),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cards = [
            Card.from_field_schemas_with_defaults(
                _MAT024_CARD0,
                **kwargs,
            ),
            OptionCardSet(
                option_spec = Mat024._option_spec_list[0],
                cards = [
                    Card.from_field_schemas_with_defaults(
                        _MAT024_OPTION0_CARD0,
                        **kwargs,
                    ),
                ],
                **kwargs
            ),
        ]
    @property
    def mid(self) -> int:
        """Get or set the Material identification."""

    @mid.setter
    def mid(self, value: int) -> None:
        pass

    @property
    def ro(self) -> float:
        """Get or set the Mass density."""

    @ro.setter
    def ro(self, value: float) -> None:
        pass

    @property
    def title(self) -> str:
        """Get or set the title."""

    @title.setter
    def title(self, value: str) -> None:
        pass


class MatPiecewise(Mat024):
    """Alias of MAT_024."""
    subkeyword = "PIECEWISE"
'''

DEFINE_TABLE = '''
_DEFINETABLE_CARD0 = (
    FieldSchema("tbid", int, 0, 10, None),
)

class DefineTable(KeywordBase):
    """DYNA DEFINE_TABLE keyword"""

    keyword = "DEFINE"
    subkeyword = "TABLE"

    def __init__(self, **kwargs):
        self._cards = [
            Card.from_field_schemas_with_defaults(
                _DEFINETABLE_CARD0,
                **kwargs,
            ),
        ]
    @property
    def tbid(self) -> int:
        """Get or set the table id."""

    @tbid.setter
    def tbid(self, value: int) -> None:
        pass
'''

MANUAL_DEFINE_TABLE = '''
from ansys.dyna.core.keywords.keyword_classes.auto.define.define_table import DefineTable as _AutoDefineTable

class DefineTable(_AutoDefineTable):
    """Manual DEFINE_TABLE with linked curves."""

    @property
    def linked_curves(self) -> list:
        """Curves of the table."""
'''

LEGACY_MAT_024 = '''
_MAT024LEGACY_CARD0 = (
    FieldSchema("old", int, 0, 10, None),
)

class Mat024Legacy(KeywordBase):
    """Old MAT_024, not exported."""

    keyword = "MAT"
    subkeyword = "024"

    def __init__(self, **kwargs):
        self._cards = [
            Card.from_field_schemas_with_defaults(
                _MAT024LEGACY_CARD0,
                **kwargs,
            ),
        ]
'''

AUTO_KEYWORDS = '''
from ansys.dyna.core.keywords.keyword_classes.auto.mat.mat_024 import Mat024, MatPiecewise
from ansys.dyna.core.keywords.keyword_classes.auto.define.define_table import DefineTable
'''

FILES = {
    "__init__.py": "from ansys.dyna.core.keywords import keywords\nfrom ansys.dyna.core.lib.deck import Deck\n" + STOP,
    "lib/__init__.py": STOP,
    "lib/keyword_base.py": KEYWORD_BASE + STOP,
    "lib/deck.py": DECK + STOP,
    "keywords/__init__.py": STOP,
    "keywords/keyword_classes/__init__.py": STOP,
    "keywords/keyword_classes/auto_keywords.py": AUTO_KEYWORDS + STOP,
    "keywords/keyword_classes/manual_keywords.py": "from ansys.dyna.core.keywords.keyword_classes.manual.define_table "
                                                   "import DefineTable\n" + STOP,
    "keywords/keyword_classes/auto/__init__.py": STOP,
    "keywords/keyword_classes/auto/mat/__init__.py": STOP,
    "keywords/keyword_classes/auto/mat/mat_024.py": MAT_024 + STOP,
    "keywords/keyword_classes/auto/define/define_table.py": DEFINE_TABLE + STOP,
    "keywords/keyword_classes/manual/define_table.py": MANUAL_DEFINE_TABLE + STOP,
    "keywords/keyword_classes/manual/mat_024_version_0.py": LEGACY_MAT_024 + STOP,
}


def make_package(base: Path) -> Path:
    root = base / "ansys" / "dyna" / "core"
    for name, text in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def catalog(tmp_path):
    return ac.Catalog(make_package(tmp_path / "site"), "9.9.9")


HEAD = "from ansys.dyna.core import Deck, keywords\n"


def check(catalog, body: str, head: str = HEAD) -> dict:
    return ck.check_source(catalog, head + textwrap.dedent(body).lstrip("\n"), "script.py")


def codes(result: dict) -> list[tuple[int, str, str]]:
    return [(i["line"], i["severity"], i["code"]) for i in result["issues"]]


# ---------------------------------------------------------------- catalog extensions

def test_alias_subclass_override_and_exported_class_in_the_catalog(catalog, tmp_path):
    assert not [m for m in list(sys.modules.values()) if str(tmp_path) in str(getattr(m, "__file__", "") or "")]
    alias = catalog.lookup("*MAT_PIECEWISE")  # own subkeyword, keyword and cards of Mat024
    assert alias["class"] == "MatPiecewise" and alias["cards_from"] == "Mat024"
    assert [f["name"] for c in alias["cards"] for f in c["fields"]] == ["mid", "ro", "title"]
    assert catalog.lookup("*MAT_024")["class"] == "Mat024"  # exported, not the legacy manual class
    assert catalog.lookup("Mat024Legacy")["class"] == "Mat024Legacy"
    api = catalog.keyword_api("DefineTable")  # auto class + members of the manual override
    assert api["complete"] and "linked_curves" in api["members"] and "tbid" in api["fields"]
    assert "activate_option" in api["members"] and "__init__" not in api["members"]
    assert catalog.keyword_api("Mat024")["fields"] == {"mid": None, "ro": None, "title": "TITLE"}


def test_resolve_and_class_members(catalog):
    assert catalog.resolve("ansys.dyna.core")["kind"] == "module"
    assert catalog.resolve("ansys.dyna")["kind"] == "module"
    assert catalog.resolve("ansys.dyna.core.keywords")["kind"] == "keyword_namespace"
    assert catalog.resolve("ansys.dyna.core.keywords.Mat024") == {"kind": "keyword_class", "name": "Mat024"}
    assert catalog.resolve("ansys.dyna.core.Deck") == {"kind": "class", "name": "ansys.dyna.core.lib.deck.Deck"}
    assert catalog.resolve("ansys.dyna.core.lib.deck.load")["kind"] == "function"
    assert catalog.resolve("ansys.dyna.core.lib.deck.VERSION")["kind"] == "variable"
    assert catalog.resolve("ansys.dyna.core.lib.deck.Deck.append")["kind"] == "member"
    assert catalog.resolve("ansys.dyna.core.keywords.keyword_classes.auto.mat.mat_024")["kind"] == "module"
    assert catalog.resolve("ansys.dyna.core.keywords.keyword_classes.auto.mat.mat_024.Mat024")["kind"] == \
        "keyword_class"
    for missing in ("ansys.dyna.core.keywords.Mat24", "ansys.dyna.core.Dek", "ansys.dyna.core.libx",
                    "ansys.dyna.core.keywords.keyword_classes.Mat24"):
        assert catalog.resolve(missing) is None
    members, complete = catalog.class_members("ansys.dyna.core.lib.deck.Deck")
    assert complete and {"append", "title", "comment_header", "remote"} <= set(members)  # self.x in any method
    _, complete = catalog.class_members("ansys.dyna.core.lib.deck.Frame")
    assert not complete  # pandas.DataFrame is not in the catalog


# ---------------------------------------------------------------- the check

def test_correct_script_has_no_issues_and_nothing_is_imported(catalog, tmp_path):
    result = check(catalog, '''
        deck = Deck(title="demo", unit="mm")
        mat = keywords.Mat024(mid=1, ro=7.85e-9, format=None, user_comment="c")
        mat.title = "steel"
        mat.ro = 7.9e-9
        deck.append(mat)
        deck.append(keywords.DefineTable(tbid=1), check=True)
        deck.title = "new"
        text = deck.write("out.k")
        curves = keywords.DefineTable().linked_curves
        other = Deck.from_text("a", "b", "c", strict=True)
        print(len(deck.keywords), mat.mid, deck.comment_header, mat.options)
        ''')
    assert result["ok"] and result["issues"] == [] and result["version"] == "9.9.9"
    assert result["checked"]["imports"] == 1 and result["checked"]["calls"] >= 6
    assert not [m for m in list(sys.modules.values()) if str(tmp_path) in str(getattr(m, "__file__", "") or "")]


def test_unknown_imports_names_and_keyword_classes_have_close_names(catalog):
    result = check(catalog, '''
        from ansys.dyna.core import Dek
        import ansys.dyna.core.libx
        from ansys.dyna.core.lib.deck import lod
        m = keywords.Mat24(mid=1)
        ''')
    assert codes(result) == [(2, "error", "unknown-import"), (3, "error", "unknown-import"),
                             (4, "error", "unknown-import"), (5, "error", "unknown-name")]
    close = [i.get("close_names", []) for i in result["issues"]]
    assert "Deck" in close[0] and "lib" in close[1] and "load" in close[2] and close[3][0] == "Mat024"
    assert not result["ok"] and result["errors"] == 4 and result["warnings"] == 0


def test_every_import_form_is_followed(catalog):
    result = check(catalog, '''
        import ansys.dyna.core
        import ansys.dyna.core as adc
        from ansys.dyna.core.keywords import keywords as kw
        from ansys.dyna.core.keywords.keyword_classes.auto.mat.mat_024 import Mat024
        from ansys.dyna.core.lib.deck import Deck as D, VERSION
        ansys.dyna.core.keywords.Mat99()
        adc.Deck().imprt_file("x.k")
        kw.Mat024(1)
        Mat024(tittle="x")
        D("t", 2)
        ''', head="")
    assert codes(result) == [(6, "error", "unknown-name"), (7, "error", "unknown-member"),
                             (8, "error", "positional-argument"), (9, "warning", "unknown-field"),
                             (10, "error", "too-many-arguments")]


def test_keyword_constructor_positional_unknown_field_and_option_card(catalog):
    result = check(catalog, '''
        a = keywords.Mat024(1, 2)
        b = keywords.Mat024(mid=1, young=2.1e5)
        c = keywords.Mat024(mid=1, title="steel")
        ''')
    assert codes(result) == [(2, "error", "positional-argument"), (3, "warning", "unknown-field"),
                             (4, "warning", "option-not-active")]
    assert 'activate_option("TITLE")' in result["issues"][2]["message"]


@pytest.mark.parametrize("switch", ['m.activate_option("TITLE")', 'm.options["TITLE"].active = True',
                                    'm.title = "steel"'])
def test_option_switched_on_later_removes_the_warning(catalog, switch):
    assert check(catalog, f'm = keywords.Mat024(mid=1, title="x")\nm.mid = 2\n{switch}\n')["issues"] == []


@pytest.mark.parametrize("body, line", [
    ('deck = Deck()\ndeck.append(keywords.Mat024(title="x"))\n', 3),  # never bound to a name
    ('m = keywords.Mat024(title="x")\nm = None\nm.activate_option("TITLE")\n', 2),  # rebound first
    ('m = keywords.Mat024(title="x")\nn = keywords.Mat024()\nn.activate_option("TITLE")\n', 2),  # other object
])
def test_option_warning_stays_when_the_option_is_not_switched_on_for_that_object(catalog, body, line):
    assert codes(check(catalog, body)) == [(line, "warning", "option-not-active")]


def test_unknown_option_name_is_a_warning_with_close_names(catalog):
    result = check(catalog, 'm = keywords.Mat024()\nm.activate_option("TITEL")\n')
    assert codes(result) == [(3, "warning", "unknown-option")] and result["issues"][0]["close_names"] == ["TITLE"]


def test_method_calls_are_checked_against_the_signatures(catalog):
    result = check(catalog, '''
        deck = Deck()
        deck.imprt_file("x.k")
        deck.import_file()
        deck.import_file("x.k", "utf-8", 3)
        deck.import_file("x.k", encodng="latin-1")
        deck.import_file(path="x.k")
        deck.import_file(*paths)
        deck.write(**options)
        Deck.from_text()
        ''')
    assert codes(result) == [(3, "error", "unknown-member"), (4, "error", "missing-argument"),
                             (5, "error", "too-many-arguments"), (6, "error", "unknown-argument"),
                             (10, "error", "missing-argument")]
    assert result["issues"][0]["close_names"][0] == "import_file"
    assert result["issues"][3]["close_names"] == ["encoding"]
    assert "path" in result["issues"][1]["message"] and "2 positional argument(s), 3 given" in \
        result["issues"][2]["message"]


def test_attributes_read_only_properties_and_new_names(catalog):
    result = check(catalog, '''
        deck = Deck()
        deck.keywords = []
        deck.titel = "x"
        deck.append = None
        print(deck.keywrds)
        mat = keywords.Mat024()
        mat.options = None
        mat.young = 2.1e5
        print(mat.youngs)
        deck.title += "x"
        ''')
    assert codes(result) == [(3, "error", "read-only"), (4, "warning", "unknown-attribute"),
                             (5, "warning", "replaces-method"), (6, "error", "unknown-member"),
                             (8, "error", "read-only"), (9, "warning", "unknown-field"),
                             (10, "error", "unknown-member")]
    assert result["issues"][1]["close_names"][0] == "title"


def test_names_from_an_unknown_base_are_not_reported(catalog):
    result = check(catalog, '''
        from ansys.dyna.core.lib.deck import Frame
        f = Frame()
        f.own()
        f.to_csv("x.csv")
        print(f.shape)
        f.anything = 1
        ''')
    assert result["issues"] == []


def test_variables_are_no_longer_followed_after_another_value(catalog):
    result = check(catalog, '''
        deck = Deck()
        deck = something_else()
        deck.anything()
        mat = keywords.Mat024()
        for mat in []:
            mat.anything()
        def build(deck):
            deck.anything()
        with open("x") as mat:
            mat.read()
        d2 = Deck()
        d2 += keywords.Mat024()
        d2.imprt()
        ''')
    assert codes(result) == [(14, "error", "unknown-member")]  # d2 += ... keeps d2 a Deck


def test_other_libraries_and_relative_imports_are_not_checked(catalog):
    result = check(catalog, '''
        import numpy as np
        from .local import thing
        np.anything(1, 2, 3)
        thing.whatever
        ''')
    assert result["issues"] == []
    result = ck.check_source(catalog, "import numpy\nnumpy.zeros(3)\n")
    assert result["ok"] and "does not import ansys.dyna.core" in result["note"]


def test_syntax_error_is_one_issue(catalog):
    result = ck.check_source(catalog, "from ansys.dyna.core import Deck\ndeck = Deck(\n", "bad.py")
    assert not result["ok"] and result["issues"][0]["code"] == "syntax" and result["issues"][0]["line"] in (2, 3)


# ---------------------------------------------------------------- the tool

@pytest.fixture
def setup(tmp_path):
    (tmp_path / "config").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(tmp_path / "config" / "config.yaml", env={}, cwd=tmp_path)
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    return context, work


def run(context, catalog, **arguments):
    wrapper = RunContextWrapper(context=context)
    return asyncio.run(run_guarded(wrapper, "check_pydyna_script", arguments,
                                   lambda: pt.script_check(context, catalog, **arguments)))


def test_tool_checks_a_file_in_the_working_folder_or_code_text(setup, catalog):
    context, work = setup
    (work / "build.py").write_text(HEAD + "keywords.Mat24()\n")
    by_path = json.loads(run(context, catalog, path="build.py"))
    assert by_path["script"] == str(work / "build.py") and codes(by_path) == [(2, "error", "unknown-name")]
    by_code = json.loads(run(context, catalog, code=HEAD + "keywords.Mat024(mid=1)\n"))
    assert by_code["ok"] and by_code["script"] == "<code>"
    assert rule_for("check_pydyna_script").right == "none"  # code text needs no access
    assert [r["tool"] for r in context.audit.recent()] == ["check_pydyna_script"] * 2


def test_tool_refuses_paths_the_policy_refuses_and_bad_arguments(setup, catalog, tmp_path):
    context, work = setup
    outside = tmp_path / "outside.py"
    outside.write_text(HEAD)
    assert run(context, catalog, path=str(outside)).startswith("BLOCKED:")  # level 1.1: working folder only
    (work / "real.py").write_text(HEAD)
    os.symlink(work / "real.py", work / "link.py")
    assert run(context, catalog, path="link.py").startswith("BLOCKED:")
    (work / "notes.txt").write_text(HEAD)
    assert "not a Python file" in run(context, catalog, path="notes.txt")
    assert "does not exist" in run(context, catalog, path="missing.py")
    (work / "big.py").write_text("#" * (pt.MAX_SCRIPT_BYTES + 1))
    assert "larger than" in run(context, catalog, path="big.py")
    (work / "latin.py").write_bytes(b"# \xe9\n")
    assert "not UTF-8" in run(context, catalog, path="latin.py")
    with pytest.raises(ToolError, match="either path"):
        pt.script_check(context, catalog)
    with pytest.raises(ToolError, match="either path"):
        pt.script_check(context, catalog, path="a.py", code="x")


# ---------------------------------------------------------------- real installed package

REAL = importlib.util.find_spec("ansys") is not None and importlib.util.find_spec("ansys.dyna.core") is not None
real_only = pytest.mark.skipif(not REAL, reason="ansys-dyna-core is not installed")

# (script body, issue codes of the check, what running it does: an exception name or "ran")
REAL_CASES = [
    ('m = keywords.Mat024(mid=1, ro=7.85e-9, e=2.1e5, pr=0.3, sigy=355.0)\nm.title = "steel"\n'
     'd = Deck(title="t")\nd.extend([m, keywords.ControlTermination(endtim=0.1)])\nd.write()\n', [], "ran"),
    ("keywords.Mat24(mid=1)\n", ["unknown-name"], "AttributeError"),
    ("keywords.Mat024(1, 2)\n", ["positional-argument"], "TypeError"),
    ('Deck().imprt_file("x.k")\n', ["unknown-member"], "AttributeError"),
    ("Deck().import_file()\n", ["missing-argument"], "TypeError"),
    ('Deck().export_file("o.k", validat=True)\n', ["unknown-argument"], "TypeError"),
    ("d = Deck()\nd.keywords = []\n", ["read-only"], "AttributeError"),
    ("m = keywords.Mat024(mid=1)\nprint(m.youngs)\n", ["unknown-member"], "AttributeError"),
    ("from ansys.dyna.core import Dek\n", ["unknown-import"], "ImportError"),
    ("m = keywords.Mat024(mid=1, young=2.1e5)\n", ["unknown-field"], "ran"),
    ('m = keywords.Mat024(mid=1, title="steel")\n', ["option-not-active"], "ran"),
    ('m = keywords.Mat024(mid=1, title="steel")\nm.activate_option("TITLE")\n', [], "ran"),
]


@real_only
@pytest.mark.parametrize("body, expected, outcome", REAL_CASES)
def test_real_package_check_agrees_with_running_the_script(body, expected, outcome, tmp_path, monkeypatch):
    catalog = ac.catalog_for()
    script = HEAD + body
    assert [i["code"] for i in ck.check_source(catalog, script)["issues"]] == expected
    monkeypatch.chdir(tmp_path)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(script, "script.py", "exec"), {})
        ran = "ran"
    except Exception as exc:  # noqa: BLE001 - the outcome is what the test compares
        ran = type(exc).__name__
    assert ran == outcome


@real_only
def test_real_package_silent_problems_lose_data_as_the_warnings_say():
    from ansys.dyna.core import keywords
    unknown = keywords.Mat024(mid=1, young=2.1e5)
    assert "young" not in unknown.write()
    not_active = keywords.Mat024(mid=1, title="steel")
    assert "steel" not in not_active.write()
    switched = keywords.Mat024(mid=1, title="steel")
    switched.activate_option("TITLE")
    assert "steel" in switched.write()


@real_only
def test_real_package_every_runtime_name_of_a_sample_of_keywords_is_in_the_catalog():
    from ansys.dyna.core import keywords
    catalog = ac.catalog_for()
    names = sorted(catalog._by_class)[::25] + ["Mat024", "DefineTable", "Mat107", "ControlTimeStep", "SectionShell"]
    for name in names:
        if not hasattr(keywords, name):
            continue
        api = catalog.keyword_api(name)
        runtime = {a for a in dir(getattr(keywords, name)()) if not a.startswith("_")}
        assert api["complete"], name
        assert runtime <= set(api["members"]) | set(api["fields"]), (name, runtime - set(api["members"]))
