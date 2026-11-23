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


def _class(node: ast.ClassDef, module: str, source_path: str) -> dict:
    members: dict[str, dict] = {}
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or item.name.startswith("_"):
            if not (isinstance(item, ast.FunctionDef) and item.name == "__init__"):
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
    return {"kind": "class", "name": node.name, "qualified_name": f"{module}.{node.name}", "module": module,
            "bases": [ast.unparse(b) for b in node.bases], "source_path": source_path, "line": node.lineno,
            "doc": _clean_doc(ast.get_docstring(node), DOC_CHARS), "members": members}


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


def _class_parts(node: ast.ClassDef) -> _ClassParts:
    parts = _ClassParts(node, [], {}, {}, [])
    for item in node.body:
        if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
            target = item.targets[0].id
            if target == "_option_spec_list" and isinstance(item.value, (ast.List, ast.Tuple)):
                parts.options = [str(_value(e.args[0])) for e in item.value.elts if isinstance(e, ast.Call) and e.args]
            elif target == "_link_fields" and isinstance(item.value, ast.Dict):
                parts.links = {str(_value(k)): ast.unparse(v).removeprefix("LinkType.")
                               for k, v in zip(item.value.keys, item.value.values) if k is not None}
        elif isinstance(item, ast.FunctionDef):
            if "property" in _decorators(item):
                doc = _clean_doc(ast.get_docstring(item), FIELD_DOC_CHARS)
                parts.docs[item.name] = doc.removeprefix("Get or set the ").removeprefix("Gets or sets the ")
            elif item.name == "__init__":
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
                "cards": [{"card": c.pop("card"), **c} for c in cards]}


def _keyword_detail(path: Path, class_name: str) -> dict:
    return _KeywordModule(path).detail(class_name)


# ---------------------------------------------------------------- the catalog

class Catalog:
    """API records of one ansys-dyna-core folder and version."""

    def __init__(self, root: Path, version: str):
        self.root = Path(root)
        self.version = version
        self.skipped: list[str] = []
        self._by_class: dict[str, list[KeywordEntry]] = {}
        self._by_keyword: dict[str, list[KeywordEntry]] = {}
        self._classes: dict[str, dict] = {}  # qualified name -> class record
        self._short: dict[str, list[str]] = {}  # short class name -> qualified names
        self._functions: dict[str, dict] = {}  # qualified name -> function record
        self._exports: dict[str, str] = {}  # name -> qualified name exported by ansys.dyna.core
        self._details: dict[tuple[Path, str], dict] = {}
        self._lock = threading.Lock()
        self._scan_keywords()
        self._scan_api()

    # -------------------------------------------------- scanning
    def _source_path(self, path: Path) -> str:
        return "ansys/dyna/core/" + path.relative_to(self.root).as_posix()

    def _scan_keywords(self) -> None:
        for sub in KEYWORD_SUBDIRS:
            folder = self.root / KEYWORD_DIR / sub
            for path in sorted(folder.rglob("*.py")) if folder.is_dir() else []:
                text = path.read_text(encoding="utf-8")
                matches = list(_CLASS_RE.finditer(text))
                for i, match in enumerate(matches):
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                    attrs = dict(_ATTR_RE.findall(text, match.end(), end))
                    if "keyword" not in attrs and "KeywordBase" not in match.group(2):
                        continue
                    keyword = None
                    if "keyword" in attrs:
                        keyword = "*" + attrs["keyword"] + ("_" + attrs["subkeyword"] if attrs.get("subkeyword") else "")
                    entry = KeywordEntry(match.group(1), keyword, path, text.count("\n", 0, match.start()) + 1)
                    self._by_class.setdefault(entry.class_name, []).append(entry)
                    if keyword:
                        self._by_keyword.setdefault(keyword, []).append(entry)

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
            module = ".".join([PACKAGE, *parts])
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError, ValueError):
                self.skipped.append(self._source_path(path))
                continue
            for item in tree.body:
                if module == PACKAGE and isinstance(item, ast.ImportFrom) and item.module:
                    for alias in item.names:
                        self._exports[alias.asname or alias.name] = f"{item.module}.{alias.name}"
                if isinstance(item, ast.ClassDef) and not item.name.startswith("_"):
                    record = _class(item, module, self._source_path(path))
                    self._classes[record["qualified_name"]] = record
                    self._short.setdefault(item.name, []).append(record["qualified_name"])
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    record = _function(item, in_class=False)
                    record.update(qualified_name=f"{module}.{item.name}", module=module,
                                  source_path=self._source_path(path))
                    self._functions[record["qualified_name"]] = record

    # -------------------------------------------------- records
    def stats(self) -> dict:
        return {"version": self.version, "keyword_classes": len(self._by_class),
                "keyword_names": len(self._by_keyword), "api_classes": len(self._classes),
                "api_members": sum(len(c["members"]) for c in self._classes.values()),
                "api_functions": len(self._functions), "skipped_files": len(self.skipped)}

    def _base(self, source_path: str, line: int) -> dict:
        return {"package": DISTRIBUTION, "version": self.version, "source_path": source_path, "line": line}

    def _import(self, qualified: str, name: str, module: str) -> str:
        exported = [n for n, q in self._exports.items() if q == qualified]
        return f"from {PACKAGE} import {exported[0]}" if exported else f"from {module} import {name}"

    def _detail(self, entry: KeywordEntry) -> dict:
        key = (entry.path, entry.class_name)
        with self._lock:
            if key not in self._details:
                try:
                    self._details[key] = _keyword_detail(entry.path, entry.class_name)
                except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                    raise CatalogError(f"cannot read {self._source_path(entry.path)}: {exc}") from None
            return self._details[key]

    def _keyword_record(self, entries: list[KeywordEntry], option: str | None = None) -> dict:
        entry = entries[-1]
        detail = self._detail(entry)
        record = {"kind": "keyword", "keyword": entry.keyword, "class": entry.class_name,
                  "import": f"from {PACKAGE} import keywords  # then keywords.{entry.class_name}()",
                  **self._base(self._source_path(entry.path), entry.line), **detail,
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
        if name.startswith(PACKAGE + "."):
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
                if option in self._detail(self._by_keyword[base][-1])["options"]:
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
        short = text.removeprefix("keywords.").removeprefix(f"{PACKAGE}.keywords.")
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
                raise CatalogError(f"{owner_q} has no public member {member} in {DISTRIBUTION} {self.version}."
                                   + self._hint(text))
        functions = [q for q in self._functions if q.rsplit(".", 1)[1] == text]
        if len(functions) == 1:
            return self._function_record(functions[0])
        ambiguous = self._ambiguous(text)
        if ambiguous:
            raise CatalogError(f"{text} is ambiguous; use one of: {', '.join(ambiguous)}")
        raise CatalogError(f"{text} is not in {DISTRIBUTION} {self.version}." + self._hint(text))

    def _hint(self, text: str) -> str:
        names = self.names()
        padded = re.sub(r"(\d+)$", lambda m: m.group(1).zfill(3), text)  # Mat24 -> Mat024, *MAT_24 -> *MAT_024
        close: list[str] = []
        for variant in dict.fromkeys((padded, text, text.upper())):
            if variant in names and variant != text:
                close.append(variant)
            close.extend(c for c in difflib.get_close_matches(variant, names, n=CLOSE_NAMES, cutoff=0.75)
                         if c not in close)
        close = close[:CLOSE_NAMES]
        return f" Close names: {', '.join(close)}." if close else " Use search_pydyna_api to find it."


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
