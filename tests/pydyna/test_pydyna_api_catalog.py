"""T1.2a: PyDyna API catalog (api_catalog.py) and the lookup_api tool.

The tests build a small fake ansys-dyna-core in tmp_path with the same layout as the real
package (keyword classes with FieldSchema tuples, OptionCardSet, CardSet helper, TableCard,
SeriesCard; lib/deck.py; a manual keyword over an auto one). Its modules stop at import,
so a test fails if the catalog ever imports them. The last test reads the real installed
package and is skipped where it is not installed.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
import textwrap
from pathlib import Path

import pytest

from champion.specialists.pydyna import api_catalog as ac
from champion.specialists.pydyna import tools as pt
from champion.tools.guard import ToolError

STOP = 'raise SystemExit("package code ran")\n'

MAT_024 = '''
import typing
from ansys.dyna.core.lib.field_schema import FieldSchema

_MAT024_CARD0 = (
    FieldSchema("mid", int, 0, 10, None),
    FieldSchema("ro", float, 10, 10, None),
    FieldSchema("fail", float, 20, 10, 1e+21),
)

_MAT024_CARD1 = (
    FieldSchema("lcss", int, 0, 10, 0),
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
    _link_fields = {
        "lcss": LinkType.DEFINE_CURVE_OR_TABLE,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cards = [
            Card.from_field_schemas_with_defaults(
                _MAT024_CARD0,
                **kwargs,
            ),
            Card.from_field_schemas_with_defaults(
                _MAT024_CARD1,
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
    def mid(self) -> typing.Optional[int]:
        """Get or set the Material identification. A unique number has to be used.
        """ # nopep8
        return self._cards[0].get_value("mid")

    @mid.setter
    def mid(self, value: int) -> None:
        """Set the mid property."""

    @property
    def ro(self) -> typing.Optional[float]:
        """Get or set the Mass density.
        """ # nopep8
        return self._cards[0].get_value("ro")
'''

SECTION_SHELL = '''
_SECTIONSHELLCARDSET_CARD0 = (
    FieldSchema("secid", int, 0, 10, None),
    FieldSchema("elform", int, 10, 10, 2),
)

class SectionShellCardSet(Cards):
    """CardSet."""

    def __init__(self, **kwargs):
        self._cards = [
            Card.from_field_schemas_with_defaults(
                _SECTIONSHELLCARDSET_CARD0,
                **kwargs,
            ),
            SeriesCard(
                "angle",
                8,
                10,
                float,
                lambda: 2,
                data = kwargs.get("angle")),
            TableCard(
                [
                    Field("xi", float, 0, 10, None),
                    Field("eta", float, 10, 10, None),
                ],
                lambda: self.nipp,
                name="integration_points",
                **kwargs,
            ),
        ]

    @property
    def secid(self) -> typing.Optional[int]:
        """Get or set the Section ID.
        """ # nopep8
        return self._cards[0].get_value("secid")

class SectionShell(KeywordBase):
    """DYNA SECTION_SHELL keyword"""

    keyword = "SECTION"
    subkeyword = "SHELL"

    def __init__(self, **kwargs):
        self._cards = [
            CardSet(
                SectionShellCardSet,
                option_specs = SectionShellCardSet._option_spec_list,
                **kwargs
            ),
        ]
'''


def mat_295(origin: str) -> str:
    return f'''
_MAT295_CARD0 = (
    FieldSchema("mid", int, 0, 10, None),
)

class Mat295(KeywordBase):
    """DYNA MAT_295 keyword ({origin})"""

    keyword = "MAT"
    subkeyword = "295"

    def __init__(self, **kwargs):
        self._cards = [
            Card.from_field_schemas_with_defaults(
                _MAT295_CARD0,
                **kwargs,
            ),
        ]
'''


DECK = '''
"""Deck module."""

class Deck:
    """Container for keywords.

    Longer text.
    """

    def __init__(self, title: str = None, **kwargs):
        self._title = title

    @property
    def title(self) -> str:
        """Title of the deck."""
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value

    @property
    def keywords(self) -> list:
        """All keywords."""
        return []

    def import_file(self, path: str, encoding: str = "utf-8") -> "DeckLoaderResult":
        """Import a keyword file.

        Parameters
        ----------
        path : str
        """

    @staticmethod
    def from_text(text: str, *parts, strict: bool = False) -> "Deck":
        """Build a deck from text."""

    def _private(self):
        pass


def load(path: str, *, validate: bool = False) -> Deck:
    """Load a deck."""


def _hidden():
    pass
'''

FILES = {
    "__init__.py": "from ansys.dyna.core.keywords import keywords\nfrom ansys.dyna.core.lib.deck import Deck\n" + STOP,
    "lib/__init__.py": STOP,
    "lib/deck.py": DECK + STOP,
    "lib/options.py": "class Options:\n    \"\"\"Lib options.\"\"\"\n" + STOP,
    "lib/_internal.py": "class Hidden:\n    pass\n",
    "lib/broken.py": "def broken(:\n",
    "run/__init__.py": STOP,
    "run/options.py": "class Options:\n    \"\"\"Run options.\"\"\"\n" + STOP,
    "keywords/__init__.py": STOP,
    "keywords/keyword_classes/__init__.py": STOP,
    "keywords/keyword_classes/auto_keywords.py": "class NotAnApiClass:\n    pass\n",
    "keywords/keyword_classes/auto/__init__.py": STOP,
    "keywords/keyword_classes/auto/mat/mat_024.py": MAT_024 + STOP,
    "keywords/keyword_classes/auto/mat/mat_295.py": mat_295("auto") + STOP,
    "keywords/keyword_classes/auto/mat/mat_999.py": 'class Mat999(KeywordBase):\n    keyword = "MAT"\n'
                                                    '    subkeyword = "999"\n    def broken(:\n',
    "keywords/keyword_classes/auto/section/section_shell.py": SECTION_SHELL + STOP,
    "keywords/keyword_classes/manual/mat_295.py": mat_295("manual") + STOP,
}


def make_package(base: Path) -> Path:
    root = base / "ansys" / "dyna" / "core"
    for name, text in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text) if not text.startswith("\n") else text)
    return root


@pytest.fixture
def catalog(tmp_path):
    return ac.Catalog(make_package(tmp_path), "9.9.9")


# ---------------------------------------------------------------- scanning

def test_catalog_counts_keywords_and_api_and_skips_private_and_broken_files(catalog):
    stats = catalog.stats()
    assert stats["version"] == "9.9.9"
    assert stats["keyword_classes"] == 4  # Mat024, Mat295 (auto + manual, one class name), Mat999, SectionShell
    assert stats["keyword_names"] == 4  # *MAT_024, *MAT_295, *MAT_999, *SECTION_SHELL (not the CardSet helper)
    assert stats["api_classes"] == 3  # Deck, lib Options, run Options; not Hidden (_internal), not auto_keywords
    assert stats["api_functions"] == 1  # load; not _hidden
    assert catalog.skipped == ["ansys/dyna/core/lib/broken.py"]


def test_nothing_of_the_package_is_imported_and_keyword_files_are_parsed_only_when_asked(catalog, tmp_path):
    # the fake modules raise SystemExit at import; none of them may be in sys.modules
    assert not [m for m in list(sys.modules.values()) if str(tmp_path) in str(getattr(m, "__file__", "") or "")]
    assert catalog._details == {}
    catalog.lookup("*MAT_024")
    assert [cls for _, cls in catalog._details] == ["Mat024"]


# ---------------------------------------------------------------- classes, methods, functions

def test_class_record_has_import_line_members_signatures_and_properties(catalog):
    record = catalog.lookup("Deck")
    assert record["kind"] == "class" and record["qualified_name"] == "ansys.dyna.core.lib.deck.Deck"
    assert record["import"] == "from ansys.dyna.core import Deck"
    assert record["package"] == "ansys-dyna-core" and record["version"] == "9.9.9"
    assert record["source_path"] == "ansys/dyna/core/lib/deck.py" and record["line"] == 4
    assert record["doc"].startswith("Container for keywords.")
    members = {m["name"]: m for m in record["members"]}
    assert list(members) == ["__init__", "title", "keywords", "import_file", "from_text"]  # no _private
    assert members["title"] == {"name": "title", "kind": "property", "type": "str", "settable": True,
                                "summary": "Title of the deck."}
    assert members["keywords"]["settable"] is False
    assert members["import_file"]["signature"] == "import_file(path: str, encoding: str = 'utf-8') -> 'DeckLoaderResult'"
    assert members["__init__"]["signature"] == "__init__(title: str = None, **kwargs)"
    assert members["from_text"]["kind"] == "staticmethod"
    assert catalog.lookup("ansys.dyna.core.lib.deck.Deck") == record


def test_method_record_has_required_and_optional_parameters(catalog):
    record = catalog.lookup("Deck.import_file")
    assert record["kind"] == "method" and record["class"] == "ansys.dyna.core.lib.deck.Deck"
    assert record["parameters"] == [
        {"name": "path", "type": "str", "default": None, "required": True},
        {"name": "encoding", "type": "str", "default": "'utf-8'", "required": False}]
    assert record["returns"] == "'DeckLoaderResult'" and record["line"] == 27 and record["version"] == "9.9.9"
    assert record["doc"].startswith("Import a keyword file.")
    static = catalog.lookup("Deck.from_text()")
    assert [p["name"] for p in static["parameters"]] == ["text", "*parts", "strict"]  # no self skipped wrongly
    assert static["parameters"][2] == {"name": "strict", "type": "bool", "default": "False", "required": False,
                                       "keyword_only": True}
    assert catalog.lookup("ansys.dyna.core.lib.deck.Deck.title")["kind"] == "property"


def test_module_function_and_ambiguous_class_name(catalog):
    record = catalog.lookup("load")
    assert record["kind"] == "function" and record["import"] == "from ansys.dyna.core.lib.deck import load"
    assert record["signature"] == "load(path: str, validate: bool = False) -> Deck"
    assert catalog.lookup("ansys.dyna.core.lib.deck.load") == record
    with pytest.raises(ac.CatalogError, match="ambiguous.*ansys.dyna.core.lib.options.Options.*"
                                              "ansys.dyna.core.run.options.Options"):
        catalog.lookup("Options")
    assert catalog.lookup("ansys.dyna.core.run.options.Options")["doc"] == "Run options."
    assert catalog.lookup("ansys.dyna.core.lib.options.Options")["doc"] == "Lib options."


# ---------------------------------------------------------------- keywords

def test_keyword_record_has_every_card_in_order_with_types_defaults_and_descriptions(catalog):
    record = catalog.lookup("*MAT_024")
    assert record["kind"] == "keyword" and record["keyword"] == "*MAT_024" and record["class"] == "Mat024"
    assert record["source_path"] == "ansys/dyna/core/keywords/keyword_classes/auto/mat/mat_024.py"
    assert record["version"] == "9.9.9" and record["options"] == ["TITLE"]
    assert record["links"] == {"lcss": "DEFINE_CURVE_OR_TABLE"}
    assert "keywords.Mat024()" in record["import"]
    cards = record["cards"]
    assert [(c["card"], c["kind"], c.get("option")) for c in cards] == [(1, "card", None), (2, "card", None),
                                                                       (3, "card", "TITLE")]
    assert cards[0]["fields"] == [
        {"name": "mid", "type": "int", "position": 0, "width": 10, "default": None,
         "description": "Material identification. A unique number has to be used."},
        {"name": "ro", "type": "float", "position": 10, "width": 10, "default": None, "description": "Mass density."},
        {"name": "fail", "type": "float", "position": 20, "width": 10, "default": 1e21}]
    assert cards[2]["fields"][0]["type"] == "str" and cards[2]["fields"][0]["width"] == 80


@pytest.mark.parametrize("name", ["Mat024", "keywords.Mat024", "ansys.dyna.core.keywords.Mat024", "*MAT_024",
                                  "MAT_024", "mat_024", " *mat_024 "])
def test_keyword_is_found_by_class_or_keyword_name(catalog, name):
    assert catalog.lookup(name)["keyword"] == "*MAT_024"


def test_keyword_with_option_suffix_and_unknown_option(catalog):
    record = catalog.lookup("*MAT_024_TITLE")
    assert record["keyword"] == "*MAT_024" and record["found_with_option"] == "TITLE"
    with pytest.raises(ac.CatalogError, match=r"\*MAT_024_ID is not in ansys-dyna-core 9.9.9"):
        catalog.lookup("*MAT_024_ID")


def test_card_set_helper_is_expanded_with_series_and_table_cards(catalog):
    cards = catalog.lookup("*SECTION_SHELL")["cards"]
    assert [(c["card"], c["kind"], c.get("name"), c.get("card_set")) for c in cards] == [
        (1, "card", None, "SectionShellCardSet"), (2, "series", "angle", "SectionShellCardSet"),
        (3, "table", "integration_points", "SectionShellCardSet")]
    assert cards[0]["fields"][0]["description"] == "Section ID." and cards[0]["fields"][1]["default"] == 2
    assert cards[1]["type"] == "float" and cards[1]["fields"] == []
    assert [f["name"] for f in cards[2]["fields"]] == ["xi", "eta"]


def test_manual_class_wins_over_auto_class_and_the_other_is_named(catalog):
    record = catalog.lookup("*MAT_295")
    assert record["source_path"] == "ansys/dyna/core/keywords/keyword_classes/manual/mat_295.py"
    assert record["doc"] == "DYNA MAT_295 keyword (manual)"
    assert record["other_classes"] == [
        {"class": "Mat295", "source_path": "ansys/dyna/core/keywords/keyword_classes/auto/mat/mat_295.py"}]


def test_broken_keyword_file_is_an_error_for_that_keyword_only(catalog):
    with pytest.raises(ac.CatalogError, match="cannot read .*mat_999.py"):
        catalog.lookup("*MAT_999")
    assert catalog.lookup("*MAT_024")["keyword"] == "*MAT_024"


# ---------------------------------------------------------------- unknown names

@pytest.mark.parametrize("name, close", [
    ("Deck.imprt_file", "Close names: Deck.import_file"),
    ("Mat24", "Close names: Mat024"),
    ("*MAT_24", "Close names: *MAT_024"),
    ("Dek", "Close names: Deck"),
])
def test_unknown_names_come_back_with_close_names(catalog, name, close):
    with pytest.raises(ac.CatalogError) as info:
        catalog.lookup(name)
    assert "9.9.9" in str(info.value) and close in str(info.value)


def test_name_far_from_everything_points_to_the_search_and_empty_name_is_an_error(catalog):
    with pytest.raises(ac.CatalogError, match="Use search_pydyna_api"):
        catalog.lookup("CompletelyDifferentThing")
    with pytest.raises(ac.CatalogError, match="name is empty"):
        catalog.lookup("  ")


# ---------------------------------------------------------------- installed package and tool

class FakeDistribution:
    def __init__(self, base: Path, version: str):
        self.base, self.version = base, version

    def locate_file(self, name):
        return self.base / name


def test_installed_package_comes_from_the_metadata_and_is_cached(tmp_path, monkeypatch):
    make_package(tmp_path)
    seen = []

    def distribution(name):
        seen.append(name)
        return FakeDistribution(tmp_path, "7.7.7")

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    monkeypatch.setattr(ac, "_CATALOGS", {})
    first = ac.catalog_for()
    assert seen == ["ansys-dyna-core"] and first.version == "7.7.7"
    assert first.root == (tmp_path / "ansys" / "dyna" / "core").resolve()
    assert ac.catalog_for() is first


def test_missing_package_is_a_clear_tool_error(tmp_path, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", missing)
    with pytest.raises(ToolError, match="ansys-dyna-core is not installed in the Python that runs Champion"):
        pt.installed_catalog()
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: FakeDistribution(tmp_path, "1.0"))
    with pytest.raises(ToolError, match="has no package files"):
        pt.installed_catalog()


def test_tool_work_returns_json_and_turns_catalog_errors_into_tool_errors(catalog):
    record = json.loads(pt.api_record(catalog, "Deck.import_file"))
    assert record["signature"].startswith("import_file(path: str")
    with pytest.raises(ToolError, match="Close names: Deck.import_file"):
        pt.api_record(catalog, "Deck.imprt_file")


# ---------------------------------------------------------------- the real installed package

def test_real_installed_package_mat_024_and_deck():
    try:
        catalog = ac.catalog_for()
    except ac.CatalogError as exc:
        pytest.skip(str(exc))
    mat = catalog.lookup("*MAT_024")
    base = [c for c in mat["cards"] if "option" not in c]
    assert sum(len(c["fields"]) for c in mat["cards"]) >= 30 and len(base) >= 4 and "TITLE" in mat["options"]
    assert mat["cards"][0]["fields"][0]["name"] == "mid" and mat["cards"][0]["fields"][0]["type"] == "int"
    assert catalog.lookup("*MAT_PIECEWISE_LINEAR_PLASTICITY")["class"] != "Mat024"
    importer = catalog.lookup("Deck.import_file")
    assert importer["parameters"][0] == {"name": "path", "type": "str", "default": None, "required": True}
    assert catalog.lookup("Deck")["import"] == "from ansys.dyna.core import Deck"
    assert catalog.stats()["keyword_names"] > 3000
