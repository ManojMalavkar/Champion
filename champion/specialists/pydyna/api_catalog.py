"""PyDyna API catalog (track T1.2a, roadmap PD-04): exact API records of the installed ansys-dyna-core.

The catalog is read from the package files of the ansys-dyna-core installed in the
Python that runs Champion. The files are parsed with ``ast``; nothing of the package is
imported or run. The version comes from the package metadata (``importlib.metadata``).

Two parts:

* Keyword classes (``keywords/keyword_classes/auto`` and ``manual``, about 3200 files):
  a fast text scan at start (class name, ``keyword``, ``subkeyword``); a file is parsed
  only when one of its keywords is looked up. A keyword record has every card in order
  (plain cards, option cards, tables, series) and every field with Python attribute name,
  type, position, width, default and the description of its property.
* The rest of the package (``Deck``, ``lib``, ``run``, ...): public classes with their
  methods and properties (signature, parameters, required or not, return type, docstring)
  and public module functions.

Names that can be looked up: ``Deck``, ``Deck.import_file``,
``ansys.dyna.core.lib.deck.Deck``, ``Mat024``, ``keywords.Mat024``, ``*MAT_024``,
``MAT_024``, ``*MAT_024_TITLE`` (a keyword with one of its options). An unknown name
raises ``CatalogError`` with close names.
"""

from __future__ import annotations

import ast
import difflib
import importlib.metadata
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from champion.specialists.pydyna.records import normalize_keyword

DISTRIBUTION = "ansys-dyna-core"
PACKAGE = "ansys.dyna.core"
KEYWORD_DIR = Path("keywords") / "keyword_classes"
KEYWORD_SUBDIRS = ("auto", "manual")  # manual after auto: the package exports the manual class last
DOC_CHARS = 1500
FIELD_DOC_CHARS = 400
SUMMARY_CHARS = 160
CLOSE_NAMES = 5

_CLASS_RE = re.compile(r"^class (\w+)\(([^)]*)\):", re.M)
_ATTR_RE = re.compile(r'^    (keyword|subkeyword) = "([^"]*)"', re.M)
KEYWORD_NAMESPACES = frozenset({  # modules that hold the keyword classes as attributes
    f"{PACKAGE}.keywords", f"{PACKAGE}.keywords.keywords", f"{PACKAGE}.keywords.keyword_classes",
    f"{PACKAGE}.keywords.keyword_classes.auto_keywords", f"{PACKAGE}.keywords.keyword_classes.manual_keywords"})
TRIVIAL_BASES = frozenset({"object", "ABC", "abc.ABC", "Generic", "typing.Generic", "Protocol", "typing.Protocol"})
_DETAIL_INTERNAL = ("properties", "methods", "attributes")
_TABLE_CALLEES = ("TableCard", "TableCardGroup")  # name is the 4th positional argument
_CARD_KINDS = {"from_field_schemas_with_defaults": "card", "TableCard": "table", "SeriesCard": "series",
               "CardSet": "card_set", "TableCardGroup": "table_group", "IncludeCard": "include"}


class CatalogError(Exception):
    """The package is missing or a name is not in the catalog (the message says which and why)."""


@dataclass(frozen=True)
class KeywordEntry:
    class_name: str
    keyword: str | None  # "*MAT_024"; None for a class without its own keyword / subkeyword
    path: Path
    line: int


def installed_package() -> tuple[Path, str]:
    """``(folder of ansys/dyna/core, version)`` of the installed ansys-dyna-core, without importing it."""
    try:
        dist = importlib.metadata.distribution(DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        raise CatalogError(f"{DISTRIBUTION} is not installed in the Python that runs Champion "
                           f"(pip install {DISTRIBUTION})") from None
    root = Path(dist.locate_file("ansys/dyna/core"))
    if not (root / "__init__.py").is_file():
        raise CatalogError(f"{DISTRIBUTION} {dist.version} is installed but {root} has no package files")
    return root, dist.version


def _text(node: ast.AST | None) -> str | None:
    return None if node is None else ast.unparse(node)


def _value(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return ast.unparse(node)


def _clean_doc(doc: str | None, limit: int) -> str:
    text = " ".join((doc or "").replace("# nopep8", "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _summary(doc: str | None) -> str:
    return _clean_doc((doc or "").strip().split("\n\n")[0], SUMMARY_CHARS)


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


# ---------------------------------------------------------------- functions, methods, classes

def _parameters(args: ast.arguments, skip_first: bool) -> list[dict]:
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    params = []
    for i, (arg, default) in enumerate(zip(positional, defaults)):
        if skip_first and i == 0:
            continue
        params.append({"name": arg.arg, "type": _text(arg.annotation), "default": _text(default),
                       "required": default is None})
    if args.vararg:
        params.append({"name": "*" + args.vararg.arg, "type": _text(args.vararg.annotation), "default": None,
                       "required": False})
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append({"name": arg.arg, "type": _text(arg.annotation), "default": _text(default),
                       "required": default is None, "keyword_only": True})
    if args.kwarg:
        params.append({"name": "**" + args.kwarg.arg, "type": _text(args.kwarg.annotation), "default": None,
                       "required": False})
    return params


def _signature(name: str, params: list[dict], returns: str | None) -> str:
    parts = []
    for p in params:
        part = p["name"] + (f": {p['type']}" if p["type"] else "")
        if p["default"] is not None:
            part += f" = {p['default']}"
        parts.append(part)
    return f"{name}({', '.join(parts)})" + (f" -> {returns}" if returns else "")


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [ast.unparse(d) for d in node.decorator_list]


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, in_class: bool) -> dict:
    decorators = _decorators(node)
    kind = "function"
    if in_class:
        kind = ("staticmethod" if "staticmethod" in decorators else
                "classmethod" if "classmethod" in decorators else "method")
    params = _parameters(node.args, skip_first=kind in ("method", "classmethod"))
    returns = _text(node.returns)
    return {"kind": kind, "name": node.name, "signature": _signature(node.name, params, returns),
            "parameters": params, "returns": returns, "line": node.lineno,
            "doc": _clean_doc(ast.get_docstring(node), DOC_CHARS)}


def _assigned_names(item: ast.AST) -> list[str]:
    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def _self_attributes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, int]]:
    """Public ``self.<name> = ...`` assignments in a method (instance attributes)."""
    found = []
    for stmt in ast.walk(func):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for t in targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
                        and not t.attr.startswith("_")):
                    found.append((t.attr, stmt.lineno))
    return found


def _attribute(name: str, line: int) -> dict:
    return {"kind": "attribute", "name": name, "type": None, "settable": True, "line": line, "doc": ""}


def _class(node: ast.ClassDef, module: str, source_path: str) -> dict:
    members: dict[str, dict] = {}
    attributes: list[tuple[str, int]] = []
    for item in node.body:
        if isinstance(item, (ast.If, ast.Try, ast.With)):  # class attributes inside try / if blocks
            for inner in _top_statements([item]):
                if isinstance(inner, (ast.Assign, ast.AnnAssign)):
                    for name in _assigned_names(inner):
                        if not name.startswith("_"):
                            members.setdefault(name, _attribute(name, inner.lineno))
            continue
        if isinstance(item, (ast.Assign, ast.AnnAssign)):  # class attributes
            for name in _assigned_names(item):
                if not name.startswith("_"):
                    members.setdefault(name, _attribute(name, item.lineno))
            continue
        if isinstance(item, ast.ClassDef) and not item.name.startswith("_"):  # nested class
            members.setdefault(item.name, _attribute(item.name, item.lineno))
            continue
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name == "__init__":
            members["__init__"] = _function(item, in_class=True)
        attributes.extend(_self_attributes(item))  # self.<name> = ... in any method
        if item.name.startswith("_"):
            continue
        decorators = _decorators(item)
        if "property" in decorators:
            members[item.name] = {"kind": "property", "name": item.name, "type": _text(item.returns),
                                  "settable": False, "line": item.lineno,
                                  "doc": _clean_doc(ast.get_docstring(item), DOC_CHARS)}
        elif any(d.endswith(".setter") for d in decorators):
            if item.name in members:
                members[item.name]["settable"] = True
        else:
            members[item.name] = _function(item, in_class=True)
    for name, line in attributes:  # after properties and methods: those win
        members.setdefault(name, _attribute(name, line))
    return {"kind": "class", "name": node.name, "qualified_name": f"{module}.{node.name}", "module": module,
            "bases": [ast.unparse(b) for b in node.bases], "source_path": source_path, "line": node.lineno,
            "doc": _clean_doc(ast.get_docstring(node), DOC_CHARS), "members": members}


def _top_names(body: list[ast.stmt]) -> set[str]:
    """Every name a module binds at top level (also inside top-level if / try / with blocks)."""
    names: set[str] = set()
    for item in body:
        if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(item.name)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            names.update(_assigned_names(item))
        elif isinstance(item, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in item.names)
        elif isinstance(item, ast.ImportFrom):
            names.update(a.asname or a.name for a in item.names if a.name != "*")
        elif isinstance(item, (ast.If, ast.With)):
            names |= _top_names(item.body) | _top_names(getattr(item, "orelse", []))
        elif isinstance(item, ast.Try):
            names |= _top_names(item.body) | _top_names(item.orelse) | _top_names(item.finalbody)
            for handler in item.handlers:
                names |= _top_names(handler.body)
    return names


def _top_statements(body: list[ast.stmt]):
    """Top-level statements of a module, also inside top-level if / try / with blocks."""
    for item in body:
        yield item
        if isinstance(item, (ast.If, ast.With)):
            yield from _top_statements(item.body)
            yield from _top_statements(getattr(item, "orelse", []))
        elif isinstance(item, ast.Try):
            for block in (item.body, item.orelse, item.finalbody, *(h.body for h in item.handlers)):
                yield from _top_statements(block)


def _imported(body: list[ast.stmt], module: str, is_package: bool) -> dict[str, str]:
    """name -> qualified name of every top-level ``from x import name`` (relative ones resolved)."""
    found: dict[str, str] = {}
    base = module if is_package else module.rpartition(".")[0]
    for item in _top_statements(body):
        if not isinstance(item, ast.ImportFrom):
            continue
        source = item.module or ""
        if item.level:
            parts = base.split(".")
            if item.level - 1 >= len(parts):
                continue
            source = ".".join(parts[:len(parts) - item.level + 1] + ([item.module] if item.module else []))
        for alias in item.names:
            if alias.name != "*":
                found[alias.asname or alias.name] = f"{source}.{alias.name}"
    return found


def _dynamic(node: ast.ClassDef) -> bool:
    """Does a class add attributes at run time (``__getattr__``, ``__setattr__``, ``setattr(self, name, ...)``,
    ``self.__dict__.update``)? Then a name missing in the catalog may exist."""
    for item in ast.walk(node):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in (
                "__getattr__", "__getattribute__", "__setattr__"):
            return True
        if (isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "setattr"
                and len(item.args) >= 2 and not isinstance(item.args[1], ast.Constant)):
            target = item.args[0]
            if ((isinstance(target, ast.Name) and target.id in ("self", "cls"))
                    or (isinstance(target, ast.Attribute) and target.attr == "__class__")):
                return True
        if (isinstance(item, ast.Attribute) and item.attr == "update" and isinstance(item.value, ast.Attribute)
                and item.value.attr == "__dict__"):
            return True
    return False


# ---------------------------------------------------------------- keyword classes

def _field(call: ast.Call) -> dict:
    args = list(call.args) + [None] * 5
    return {"name": _value(args[0]), "type": _text(args[1]), "position": _value(args[2]),
            "width": _value(args[3]), "default": _value(args[4])}


def _fields(node: ast.AST, tuples: dict[str, list[dict]]) -> list[dict]:
    """Fields of one card element in source order: inline Field / FieldSchema calls and module tuples."""
    if isinstance(node, ast.Call) and _callee(node) in ("Field", "FieldSchema"):
        return [_field(node)]
    if isinstance(node, ast.Name) and node.id in tuples:
        return [dict(f) for f in tuples[node.id]]
    found: list[dict] = []
    for child in ast.iter_child_nodes(node):
        found.extend(_fields(child, tuples))
    return found


def _option_index(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return node.slice.value if isinstance(node.slice.value, int) else None
    return None


@dataclass
class _ClassParts:
    node: ast.ClassDef
    options: list[str]
    links: dict[str, str]
    docs: dict[str, str]
    elements: list[ast.AST]
    properties: dict[str, bool]  # property name -> settable
    methods: dict[str, dict]  # public methods of the keyword class itself
    attributes: list[str]  # public class attributes (keyword, subkeyword, ...)


def _class_parts(node: ast.ClassDef) -> _ClassParts:
    parts = _ClassParts(node, [], {}, {}, [], {}, {}, [])
    for item in node.body:
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            parts.attributes.extend(n for n in _assigned_names(item) if not n.startswith("_"))
        elif isinstance(item, ast.ClassDef) and not item.name.startswith("_"):  # nested class
            parts.attributes.append(item.name)
        if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
            target = item.targets[0].id
            if target == "_option_spec_list" and isinstance(item.value, (ast.List, ast.Tuple)):
                parts.options = [str(_value(e.args[0])) for e in item.value.elts if isinstance(e, ast.Call) and e.args]
            elif target == "_link_fields" and isinstance(item.value, ast.Dict):
                parts.links = {str(_value(k)): ast.unparse(v).removeprefix("LinkType.")
                               for k, v in zip(item.value.keys, item.value.values) if k is not None}
        elif isinstance(item, ast.FunctionDef):
            decorators = _decorators(item)
            if "property" in decorators:
                doc = _clean_doc(ast.get_docstring(item), FIELD_DOC_CHARS)
                parts.docs[item.name] = doc.removeprefix("Get or set the ").removeprefix("Gets or sets the ")
                parts.properties.setdefault(item.name, False)
            elif any(d.endswith(".setter") for d in decorators):
                parts.properties[item.name] = True
            elif not item.name.startswith("_"):
                parts.methods[item.name] = _function(item, in_class=True)
            elif item.name == "__init__":
                parts.attributes.extend(name for name, _ in _self_attributes(item))
                for stmt in ast.walk(item):
                    if (isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Attribute)
                            and stmt.targets[0].attr == "_cards" and isinstance(stmt.value, ast.List)):
                        parts.elements = list(stmt.value.elts)
    return parts


class _KeywordModule:
    """One keyword file: module-level field tuples and classes (the keyword and its card-set helpers)."""

    def __init__(self, path: Path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.path = path
        self.tuples: dict[str, list[dict]] = {}
        self.classes: dict[str, _ClassParts] = {}
        for item in tree.body:
            if (isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name)
                    and isinstance(item.value, ast.Tuple)):
                self.tuples[item.targets[0].id] = [_field(e) for e in item.value.elts if isinstance(e, ast.Call)
                                                   and _callee(e) in ("FieldSchema", "Field")]
            elif isinstance(item, ast.ClassDef):
                self.classes[item.name] = _class_parts(item)

    def cards(self, element: ast.AST, owner: _ClassParts, option: str | None, depth: int = 0) -> list[dict]:
        if not isinstance(element, ast.Call):
            return [{"kind": "other", "source": _clean_doc(ast.unparse(element), SUMMARY_CHARS), "fields": []}]
        callee = _callee(element)
        keywords = {k.arg: k.value for k in element.keywords if k.arg}
        if callee == "OptionCardSet":
            index = _option_index(keywords.get("option_spec"))
            name = owner.options[index] if index is not None and index < len(owner.options) else None
            inner = keywords.get("cards")
            found: list[dict] = []
            for item in inner.elts if isinstance(inner, (ast.List, ast.Tuple)) else []:
                found.extend(self.cards(item, owner, name or option, depth))
            return found
        helper = element.args[0] if element.args else None
        if (callee == "CardSet" and isinstance(helper, ast.Name) and helper.id in self.classes and depth < 3):
            inner_parts = self.classes[helper.id]  # the card set's own cards, repeated in the deck
            found = []
            for item in inner_parts.elements:
                for card in self.cards(item, inner_parts, option, depth + 1):
                    card.setdefault("card_set", helper.id)
                    for f in card["fields"]:
                        if f["name"] in inner_parts.docs:
                            f.setdefault("description", inner_parts.docs[f["name"]])
                    found.append(card)
            return found
        card: dict[str, Any] = {"kind": _CARD_KINDS.get(callee, callee.lower() or "other")}
        if option:
            card["option"] = option
        if callee == "SeriesCard":
            args = list(element.args) + [None] * 4
            card.update(name=_value(args[0]), type=_text(args[3]))
        elif "name" in keywords:
            card["name"] = _value(keywords["name"])
        elif callee in _TABLE_CALLEES and len(element.args) > 3 and isinstance(_value(element.args[3]), str):
            # TableCard / TableCardGroup(fields, length_func, active_func, name, ...): PyDyna takes the
            # table name positionally, and the keyword accepts it as a constructor argument
            # (Part(parts=df), Node(nodes=df)); found 28 Nov 2026 with the T1.2 template.
            card["name"] = _value(element.args[3])
        card["fields"] = _fields(element, self.tuples)
        return [card]

    def detail(self, class_name: str) -> dict:
        parts = self.classes.get(class_name)
        if parts is None:
            raise CatalogError(f"class {class_name} not found in {self.path.name}")
        cards: list[dict] = []
        for element in parts.elements:
            cards.extend(self.cards(element, parts, None))
        for number, card in enumerate(cards, 1):
            card["card"] = number
            for f in card["fields"]:
                if f["name"] in parts.docs:
                    f.setdefault("description", parts.docs[f["name"]])
        node = parts.node
        return {"doc": _clean_doc(ast.get_docstring(node), DOC_CHARS), "bases": [ast.unparse(b) for b in node.bases],
                "options": parts.options, "links": parts.links,
                "cards": [{"card": c.pop("card"), **c} for c in cards],
                # for the script check (T1.2b), not shown by lookup:
                "properties": dict(parts.properties), "methods": dict(parts.methods),
                "attributes": list(parts.attributes)}


def _keyword_detail(path: Path, class_name: str) -> dict:
    return _KeywordModule(path).detail(class_name)


# ---------------------------------------------------------------- the catalog

class Catalog:
    """API records of one ansys-dyna-core folder and version."""

    def __init__(self, root: Path, version: str, package: str = PACKAGE, distribution: str = DISTRIBUTION,
                 search_tool: str = "search_pydyna_api"):
        self.root = Path(root)
        self.version = version
        self.package = package
        self.distribution = distribution
        self.search_tool = search_tool  # named in the hint of an unknown name
        self.peers: list[Catalog] = []  # catalogs of the packages this one imports from (DPF: post and core)
        self.skipped: list[str] = []
        self._by_class: dict[str, list[KeywordEntry]] = {}
        self._by_keyword: dict[str, list[KeywordEntry]] = {}
        self._classes: dict[str, dict] = {}  # qualified name -> class record
        self._short: dict[str, list[str]] = {}  # short class name -> qualified names
        self._functions: dict[str, dict] = {}  # qualified name -> function record
        self._exports: dict[str, str] = {}  # name -> qualified name exported by the package itself
        self._imports: dict[str, dict[str, str]] = {}  # module -> name -> qualified name of a from-import
        self._dynamic: set[str] = set()  # classes that add attributes at run time (__getattr__, setattr)
        self._open: set[str] = set()  # modules with names the catalog cannot list (import *, __getattr__)
        self._details: dict[tuple[Path, str], dict] = {}
        self._modules: set[str] = set()  # parsed modules outside the keyword classes
        self._module_names: dict[str, set[str]] = {}  # module -> every top-level name (variables too)
        # manual subclasses of an auto keyword class with the same name and no keyword of their own
        # (for example manual DefineTable(_AutoDefineTable)): the package exports them; lookups show
        # the auto class, the script check adds their members
        self._overrides: dict[str, KeywordEntry] = {}
        self._lock = threading.Lock()
        self._scan_keywords()
        self._scan_api()

    # -------------------------------------------------- scanning
    def _source_path(self, path: Path) -> str:
        return self.package.replace(".", "/") + "/" + path.relative_to(self.root).as_posix()

    def _scan_keywords(self) -> None:
        raw: dict[str, tuple[str | None, str | None]] = {}  # class -> (keyword, subkeyword) as in the source
        for sub in KEYWORD_SUBDIRS:
            folder = self.root / KEYWORD_DIR / sub
            for path in sorted(folder.rglob("*.py")) if folder.is_dir() else []:
                text = path.read_text(encoding="utf-8")
                matches = list(_CLASS_RE.finditer(text))
                for i, match in enumerate(matches):
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                    attrs = dict(_ATTR_RE.findall(text, match.end(), end))
                    name = match.group(1)
                    line = text.count("\n", 0, match.start()) + 1
                    bases = [b.strip().split("[")[0] for b in match.group(2).split(",")]
                    # an alias subclass such as Mat107(MatModifiedJohnsonCook) with only its own subkeyword
                    parent = next((b for b in bases if b in raw and b != name), None)
                    if "keyword" not in attrs and "KeywordBase" not in bases and not (parent and "subkeyword" in attrs):
                        if sub == "manual" and name in self._by_class and not name.startswith("_"):
                            self._overrides[name] = KeywordEntry(name, self._by_class[name][-1].keyword, path, line)
                        continue
                    inherited = raw.get(parent, (None, None)) if parent else (None, None)
                    word = attrs.get("keyword", inherited[0])
                    subword = attrs["subkeyword"] if "subkeyword" in attrs else inherited[1]
                    raw[name] = (word, subword)
                    # PyDyna's KeywordBase._get_base_title: a subkeyword equal to the keyword is not
                    # repeated (Part -> *PART, Node -> *NODE, not *PART_PART); found 28 Nov 2026.
                    keyword = ("*" + word + ("_" + subword if subword and subword != word else "")
                               if word else None)
                    entry = KeywordEntry(name, keyword, path, line)
                    self._by_class.setdefault(entry.class_name, []).append(entry)
                    if keyword:
                        self._by_keyword.setdefault(keyword, []).append(entry)
        # The class used for a name is the last one: the one the package exports (auto_keywords.py,
        # manual_keywords.py), manual before auto. Not exported: legacy versions such as
        # manual/mat_295_version_0_9_1.py (Mat295Legacy, also *MAT_295).
        exported = self._exported_keyword_classes()
        if exported:
            def rank(entry: KeywordEntry) -> tuple[bool, bool]:
                return entry.class_name in exported, (self.root / KEYWORD_DIR / "manual") in entry.path.parents
            for entries in (*self._by_class.values(), *self._by_keyword.values()):
                entries.sort(key=rank)

    def _exported_keyword_classes(self) -> set[str]:
        names: set[str] = set()
        for module in ("auto_keywords.py", "manual_keywords.py"):
            path = self.root / KEYWORD_DIR / module
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names.update(a.asname or a.name for a in node.names if a.name != "*")
        return names

    def _scan_api(self) -> None:
        keyword_root = self.root / KEYWORD_DIR
        for path in sorted(self.root.rglob("*.py")):
            if keyword_root in path.parents:
                continue
            parts = list(path.relative_to(self.root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if any(p.startswith("_") for p in parts):
                continue
            module = ".".join([self.package, *parts])
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError, ValueError):
                self.skipped.append(self._source_path(path))
                continue
            self._modules.add(module)
            self._module_names[module] = _top_names(tree.body)
            self._imports[module] = _imported(tree.body, module, path.name == "__init__.py")
            if "__getattr__" in self._module_names[module] or any(
                    isinstance(i, ast.ImportFrom) and any(a.name == "*" for a in i.names)
                    for i in _top_statements(tree.body)):
                self._open.add(module)
            self._module_parsed(module, tree, path)
            for item in tree.body:
                if isinstance(item, ast.ClassDef) and not item.name.startswith("_"):
                    record = _class(item, module, self._source_path(path))
                    self._classes[record["qualified_name"]] = record
                    if _dynamic(item):
                        self._dynamic.add(record["qualified_name"])
                    self._short.setdefault(item.name, []).append(record["qualified_name"])
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    record = _function(item, in_class=False)
                    record.update(qualified_name=f"{module}.{item.name}", module=module,
                                  source_path=self._source_path(path))
                    self._functions[record["qualified_name"]] = record
        self._exports = self._imports.get(self.package, {})

    def _module_parsed(self, module: str, tree: ast.Module, path: Path) -> None:
        """Called for every parsed module (a subclass collects more, as the DPF operators)."""

    # -------------------------------------------------- records
    def stats(self) -> dict:
        return {"version": self.version, "keyword_classes": len(self._by_class),
                "keyword_names": len(self._by_keyword), "api_classes": len(self._classes),
                "api_members": sum(len(c["members"]) for c in self._classes.values()),
                "api_functions": len(self._functions), "skipped_files": len(self.skipped)}

    def _base(self, source_path: str, line: int) -> dict:
        return {"package": self.distribution, "version": self.version, "source_path": source_path, "line": line}

    def _import(self, qualified: str, name: str, module: str) -> str:
        exported = [n for n, q in self._exports.items() if q == qualified]
        return f"from {self.package} import {exported[0]}" if exported else f"from {module} import {name}"

    def _detail(self, entry: KeywordEntry) -> dict:
        key = (entry.path, entry.class_name)
        with self._lock:
            if key not in self._details:
                try:
                    self._details[key] = _keyword_detail(entry.path, entry.class_name)
                except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                    raise CatalogError(f"cannot read {self._source_path(entry.path)}: {exc}") from None
            return self._details[key]

    def _cards_detail(self, entry: KeywordEntry) -> dict:
        """The detail of a keyword class; an alias subclass without cards of its own (Mat107 of
        MatModifiedJohnsonCook) gets the cards, options and links of its keyword base class."""
        detail = self._detail(entry)
        if detail["cards"]:
            return detail
        for base in detail["bases"]:
            base = base.split("[")[0]
            if base in self._by_class and base != entry.class_name:
                inner = self._cards_detail(self._by_class[base][-1])
                return {**detail, "cards": inner["cards"], "options": detail["options"] or inner["options"],
                        "links": detail["links"] or inner["links"], "cards_from": base}
        return detail

    def _keyword_record(self, entries: list[KeywordEntry], option: str | None = None) -> dict:
        entry = entries[-1]
        detail = self._cards_detail(entry)
        record = {"kind": "keyword", "keyword": entry.keyword, "class": entry.class_name,
                  "import": f"from {self.package} import keywords  # then keywords.{entry.class_name}()",
                  **self._base(self._source_path(entry.path), entry.line),
                  **{k: v for k, v in detail.items() if k not in _DETAIL_INTERNAL},
                  "note": "field names are the Python attribute names (lower case); in the deck they are upper case"}
        if option:
            record["found_with_option"] = option
        if len(entries) > 1:
            record["other_classes"] = [{"class": e.class_name, "source_path": self._source_path(e.path)}
                                       for e in entries[:-1]]
        return record

    def _class_record(self, qualified: str) -> dict:
        cls = self._classes[qualified]
        members = [{"name": m["name"], "kind": m["kind"],
                    **({"signature": m["signature"]} if "signature" in m else
                       {"type": m.get("type"), "settable": m.get("settable", False)}),
                    "summary": _summary(m["doc"])} for m in cls["members"].values()]
        return {"kind": "class", "name": cls["name"], "qualified_name": qualified,
                "import": self._import(qualified, cls["name"], cls["module"]),
                **self._base(cls["source_path"], cls["line"]), "bases": cls["bases"], "doc": cls["doc"],
                "members": members}

    def _member_record(self, qualified: str, member: str) -> dict:
        cls = self._classes[qualified]
        record = dict(cls["members"][member])
        return {**record, "class": qualified, **self._base(cls["source_path"], record.pop("line"))}

    def _function_record(self, qualified: str) -> dict:
        record = dict(self._functions[qualified])
        return {**record, "import": f"from {record.pop('module')} import {record['name']}",
                **self._base(record.pop("source_path"), record.pop("line"))}

    def _resolve_class(self, name: str) -> str | None:
        if name in self._classes:
            return name
        if name.startswith(self.package + "."):
            return None
        if name in self._exports and self._exports[name] in self._classes:
            return self._exports[name]
        found = self._short.get(name, [])
        return found[0] if len(found) == 1 else None

    def _ambiguous(self, name: str) -> list[str]:
        return self._short.get(name, []) if len(self._short.get(name, [])) > 1 else []

    def _keyword_by_name(self, name: str) -> dict | None:
        keyword = normalize_keyword(name)
        if keyword in self._by_keyword:
            return self._keyword_record(self._by_keyword[keyword])
        parts = keyword.split("_")
        for cut in range(len(parts) - 1, 0, -1):  # *MAT_024_TITLE -> *MAT_024 with option TITLE
            base, option = "_".join(parts[:cut]), "_".join(parts[cut:])
            if base in self._by_keyword:
                if option in self._cards_detail(self._by_keyword[base][-1])["options"]:
                    return self._keyword_record(self._by_keyword[base], option)
        return None

    def names(self) -> list[str]:
        members = [f"{c['name']}.{m}" for c in self._classes.values() for m in c["members"]]
        return [*self._by_keyword, *self._by_class, *self._short, *members,
                *(f.rsplit(".", 1)[1] for f in self._functions)]

    def lookup(self, name: str) -> dict:
        """The record of a keyword, class, method, property or function (see the module docstring)."""
        text = str(name or "").strip().removesuffix("()")
        if not text:
            raise CatalogError("name is empty")
        if text.startswith("*") or re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)+", text):
            record = self._keyword_by_name(text)
            if record:
                return record
        short = text.removeprefix("keywords.").removeprefix(f"{self.package}.keywords.")
        if short in self._by_class and not self._resolve_class(short):
            return self._keyword_record(self._by_class[short])
        qualified = self._resolve_class(text)
        if qualified:
            return self._class_record(qualified)
        if text in self._functions:
            return self._function_record(text)
        if "." in text:
            owner, member = text.rsplit(".", 1)
            owner_q = self._resolve_class(owner)
            if owner_q and member in self._classes[owner_q]["members"]:
                return self._member_record(owner_q, member)
            if owner_q:
                raise CatalogError(f"{owner_q} has no public member {member} in {self.distribution} {self.version}."
                                   + self._hint(text))
        functions = [q for q in self._functions if q.rsplit(".", 1)[1] == text]
        if len(functions) == 1:
            return self._function_record(functions[0])
        ambiguous = self._ambiguous(text)
        if ambiguous:
            raise CatalogError(f"{text} is ambiguous; use one of: {', '.join(ambiguous)}")
        raise CatalogError(f"{text} is not in {self.distribution} {self.version}." + self._hint(text))

    # -------------------------------------------------- for the script check (api_check.py, T1.2b)
    def resolve(self, path: str, _depth: int = 0, as_module: bool = False) -> dict | None:
        """What a dotted name under the package is: module, keyword_namespace, keyword_class, class,
        function or member (``{"kind": ..., "name": ...}``), or None when the catalog does not know it.
        A name under a peer package is resolved by that peer. ``as_module`` (an import statement):
        a module file wins over a name its parent imports (``import a.b`` loads the file a/b.py)."""
        if path == self.package or self.package.startswith(path + "."):
            return {"kind": "module", "name": path}
        if not path.startswith(self.package + "."):
            peer = self._peer_for(path)
            return peer.resolve(path, _depth, as_module) if peer is not None and _depth < 4 else None
        if path in KEYWORD_NAMESPACES:
            return {"kind": "keyword_namespace", "name": path}
        parent, _, name = path.rpartition(".")
        if parent in KEYWORD_NAMESPACES and name in self._by_class:
            return {"kind": "keyword_class", "name": name}
        if as_module and (path in self._modules or self._module_file(path)):
            return {"kind": "module", "name": path}
        target = self._imports.get(parent, {}).get(name)
        if target and target != path and _depth < 3:  # from x import name in parent (wins over a submodule name)
            found = self.resolve(target, _depth + 1)
            if found is not None:
                return found
        if path in self._modules or self._module_file(path):
            return {"kind": "module", "name": path}
        if (parent not in KEYWORD_NAMESPACES and self._module_file(parent)
                and (self.root / KEYWORD_DIR) in self._module_file(parent).parents):
            # a name in one keyword file (keyword_classes.auto.mat.mat_024.Mat024): keyword class or not checked
            if name in self._by_class:
                return {"kind": "keyword_class", "name": name}
            return {"kind": "variable", "name": path}
        if path in self._classes:
            return {"kind": "class", "name": path}
        if path in self._functions:
            return {"kind": "function", "name": path}
        if name in self._module_names.get(parent, ()):  # a variable, a private or an imported name
            return {"kind": "variable", "name": path}
        if parent in self._open or any(part.startswith("_") for part in parent.split(".")):
            return {"kind": "variable", "name": path}  # import * / __getattr__ / private module: not checked
        owner = self.resolve(parent, _depth + 1) if parent and _depth < 3 else None
        if owner is not None and owner["kind"] == "module" and owner["name"] != parent:
            return self.resolve(f"{owner['name']}.{name}", _depth + 1)  # parent is an imported module
        if owner is not None and owner["kind"] == "class":  # also through an export: ansys.dyna.core.Deck.write
            members, _ = self.class_members(owner["name"])
            if name in members:
                return {"kind": "member", "name": f"{owner['name']}.{name}", "class": owner["name"]}
        return None

    def _module_file(self, path: str) -> Path | None:
        """The file or package folder of a dotted module name under the package, if it exists."""
        if not path.startswith(self.package + "."):
            return None
        base = self.root.joinpath(*path[len(self.package) + 1:].split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py", base):
            if candidate.exists() and (candidate.suffix == ".py" or candidate.is_dir()):
                return candidate
        return None

    def module_members(self, module: str) -> list[str]:
        """Public names of a module (for close names): classes, functions, submodules, exports."""
        if module in KEYWORD_NAMESPACES:
            return list(self._by_class)
        names = [q.rsplit(".", 1)[1] for q in (*self._classes, *self._functions, *self._modules)
                 if q.rpartition(".")[0] == module]
        names += sorted(n for n in self._module_names.get(module, ()) if not n.startswith("_"))
        return list(dict.fromkeys(names))

    def _peer_for(self, path: str) -> Catalog | None:
        return next((p for p in self.peers if path == p.package or path.startswith(p.package + ".")), None)

    def _owner(self, qualified: str) -> Catalog | None:
        """The catalog (this one or a peer) that has a class."""
        return next((c for c in (self, *self.peers) if qualified in c._classes), None)

    def _base_class(self, base: str, module: str) -> str | None:
        """The qualified name of a class named in ``module`` (a base or an annotation), if known."""
        base = base.split("[")[0].strip()
        for candidate in (base, f"{module}.{base}", self._imports.get(module, {}).get(base)):
            if candidate and self._owner(candidate):
                return candidate
        for catalog in (self, *self.peers):
            found = catalog._short.get(base.rsplit(".", 1)[-1], [])
            if len(found) == 1:
                return found[0]
        return None

    def class_members(self, qualified: str) -> tuple[dict, bool]:
        """Public members of a class and its bases, and whether every base is in the catalog
        (when one is not, a missing name may come from it, so the check stays silent)."""
        members: dict[str, dict] = {}
        complete, seen, queue = True, set(), [qualified]
        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            owner = self._owner(name)
            if owner is None:  # not a class of the catalog (a class made at run time): nothing to list
                complete = False
                continue
            cls = owner._classes[name]
            if name in owner._dynamic:  # names added at run time cannot be listed
                complete = False
            for member, record in cls["members"].items():
                members.setdefault(member, record)
            for base in cls["bases"]:
                found = owner._base_class(base, cls["module"])
                if found:
                    queue.append(found)
                elif base not in TRIVIAL_BASES and not base.startswith("metaclass"):
                    complete = False
        return members, complete

    def _defined_in(self, qualified: str, member: str) -> tuple[dict, str] | None:
        """(record, module) of the class in the bases of ``qualified`` that defines ``member``."""
        seen, queue = set(), [qualified]
        while queue:
            name = queue.pop(0)
            owner = self._owner(name) if name not in seen else None
            seen.add(name)
            if owner is None:
                continue
            cls = owner._classes[name]
            if member in cls["members"]:
                return cls["members"][member], cls["module"]
            queue.extend(found for base in cls["bases"] if (found := owner._base_class(base, cls["module"])))
        return None

    def _annotated_class(self, text: str | None, module: str) -> str | None:
        if not text:
            return None
        text = text.strip().strip("'\"").split("[")[0].strip()
        if not text or "|" in text or text in ("None", "Any", "Self"):
            return None
        found = self._base_class(text, module)
        return found if found and self._owner(found) else None

    def member_type(self, qualified: str, member: str, call: bool = False) -> str | None:
        """The class that a property (``call=False``) or a method call (``call=True``) of a class
        returns, from its annotation, when the catalog has that class."""
        found = self._defined_in(qualified, member)
        if found is None:
            return None
        record, module = found
        if call and record.get("kind") in ("method", "staticmethod", "classmethod"):
            return self._annotated_class(record.get("returns"), module)
        if not call and record.get("kind") == "property":
            return self._annotated_class(record.get("type"), module)
        return None

    def function_type(self, qualified: str) -> str | None:
        """The class a module function returns, from its annotation."""
        owner = next((c for c in (self, *self.peers) if qualified in c._functions), None)
        if owner is None:
            return None
        record = owner._functions[qualified]
        return owner._annotated_class(record.get("returns"), record["module"])

    def related(self, name: str) -> bool:
        """Is a module name the package (or a peer), one of its parents or one of its submodules?"""
        return any(name == p or name.startswith(p + ".") or p.startswith(name + ".")
                   for p in (self.package, *(c.package for c in self.peers)))

    def label(self, path: str | None = None) -> str:
        """Distribution and version of the package a name belongs to."""
        owner = self._peer_for(path) if path and not path.startswith(self.package) else None
        owner = owner or self
        return f"{owner.distribution} {owner.version}"

    def keyword_api(self, class_name: str, _depth: int = 0) -> dict:
        """What a script may use on a keyword class: constructor fields (with their option), properties
        (settable or not), methods and attributes of the class and its bases."""
        entries = self._by_class[class_name]
        entry = entries[-1]
        detail = self._cards_detail(entry)
        fields: dict[str, str | None] = {}
        for card in detail["cards"]:
            names = [f["name"] for f in card["fields"]] + ([card["name"]] if card.get("name") else [])
            for name in names:
                if isinstance(name, str):
                    fields.setdefault(name, card.get("option"))
        members: dict[str, dict] = {n: {"kind": "property", "name": n, "settable": s}
                                    for n, s in detail["properties"].items()}
        for name, record in detail["methods"].items():
            members.setdefault(name, record)
        for name in detail["attributes"]:
            members.setdefault(name, _attribute(name, entry.line))
        override = self._overrides.get(class_name)
        if override is not None and _depth == 0:
            extra = self._detail(override)
            for name, settable in extra["properties"].items():
                members[name] = {"kind": "property", "name": name, "settable": settable}
            for name, record in extra["methods"].items():
                members[name] = record
            for name in extra["attributes"]:
                members.setdefault(name, _attribute(name, override.line))
        complete = True
        for base in detail["bases"]:
            base = base.split("[")[0]
            if base in self._by_class and base != class_name and _depth < 3:
                inner = self.keyword_api(base, _depth + 1)
                for name, option in inner["fields"].items():
                    fields.setdefault(name, option)
                for name, record in inner["members"].items():
                    members.setdefault(name, record)
                complete = complete and inner["complete"]
                continue
            found = self._base_class(base, "")
            if found:
                inherited, base_complete = self.class_members(found)
                for name, record in inherited.items():
                    if name != "__init__":
                        members.setdefault(name, record)
                complete = complete and base_complete
            elif base not in TRIVIAL_BASES:
                complete = False
        return {"keyword": entry.keyword, "class": class_name, "fields": fields, "members": members,
                "options": list(detail["options"]), "complete": complete}

    def close_names(self, text: str, names: list[str]) -> list[str]:
        padded = re.sub(r"(\d+)$", lambda m: m.group(1).zfill(3), text)  # Mat24 -> Mat024, *MAT_24 -> *MAT_024
        close: list[str] = []
        for variant in dict.fromkeys((padded, text, text.upper())):
            if variant in names and variant != text:
                close.append(variant)
            close.extend(c for c in difflib.get_close_matches(variant, names, n=CLOSE_NAMES, cutoff=0.75)
                         if c not in close)
        return close[:CLOSE_NAMES]

    def _hint(self, text: str) -> str:
        close = self.close_names(text, self.names())
        return f" Close names: {', '.join(close)}." if close else f" Use {self.search_tool} to find it."


_CATALOGS: dict[tuple[Path, str], Catalog] = {}
_CATALOGS_LOCK = threading.Lock()


def catalog_for(root: Path | None = None, version: str | None = None) -> Catalog:
    """The catalog of ``root`` (default: the installed ansys-dyna-core), built once per folder and version."""
    if root is None:
        root, version = installed_package()
    key = (Path(root).resolve(), str(version))
    with _CATALOGS_LOCK:
        if key not in _CATALOGS:
            _CATALOGS[key] = Catalog(key[0], key[1])
        return _CATALOGS[key]
