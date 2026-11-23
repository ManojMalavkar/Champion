"""PyDyna API check of a script (track T1.2b, roadmap PD-05, PyDyna part).

A static check of a Python script against the installed ansys-dyna-core, through the API
catalog of T1.2a (``api_catalog.py``). The script is parsed with ``ast``; nothing is run or
imported. Only names that the script binds to ``ansys.dyna.core`` are checked:

* imports: ``from ansys.dyna.core import Deck, keywords``, ``import ansys.dyna.core as adc``,
  ``from ansys.dyna.core.lib.deck import Deck`` ... must exist;
* names under a module or the keyword namespace: ``keywords.Mat024`` must exist
  (``keywords.Mat24`` is an error with close names);
* calls of classes and functions: unknown keyword arguments, missing required arguments,
  too many positional arguments (from the signatures in the package);
* keyword classes (``keywords.Mat024(...)``): positional arguments are an error (they take
  field names only); a name that is not a field is a warning, because PyDyna ignores it
  without an error; a field on an option card (``title=`` of TITLE) is a warning when the
  option is not switched on later in the script (``activate_option("TITLE")``, setting the
  property, or ``options["TITLE"].active = True``): as a constructor argument it does not
  switch the option on, so the card is not written (checked with ansys-dyna-core 0.12.1);
* variables made from a class or keyword class (``deck = Deck()``, ``mat = keywords.Mat024()``):
  unknown attributes and methods are errors (AttributeError when run), method calls are
  checked like functions, setting a read-only property is an error, setting a name that is
  not a field or attribute of a keyword is a warning (Python keeps it, the deck does not).

When a class has a base outside the package, a missing member may come from that base:
then nothing is reported for it. Variables are followed in source order through the whole
file (one scope); a variable bound to anything else is no longer followed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from champion.specialists.pydyna.api_catalog import DISTRIBUTION, PACKAGE, Catalog, CatalogError

ERROR, WARNING = "error", "warning"
# keyword arguments every keyword class reads besides its fields (KeywordBase, Cards, CardSet)
GENERIC_KEYWORD_ARGS = frozenset({"format", "user_comment", "parameter_set", "keyword_id", "uri_prefix"})
NOTE = ("Static check against the installed package: only names bound to ansys.dyna.core are checked; "
        "nothing was run. Values, units and the deck logic are not checked.")


@dataclass(frozen=True)
class Issue:
    line: int
    column: int
    severity: str
    code: str
    message: str
    close: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        record = {"line": self.line, "column": self.column, "severity": self.severity, "code": self.code,
                  "message": self.message}
        if self.close:
            record["close_names"] = list(self.close)
        return record


@dataclass(frozen=True)
class _Value:
    kind: str  # "path" (dotted name under the package), "instance" (of a class), "keyword" (keyword object)
    name: str  # the dotted path, the qualified class name or the keyword class name


def _related(name: str) -> bool:
    """Is a module name ansys.dyna.core, one of its parents or one of its submodules?"""
    return name == PACKAGE or name.startswith(PACKAGE + ".") or PACKAGE.startswith(name + ".")


def _short(qualified: str) -> str:
    return qualified.rsplit(".", 1)[-1]


class _Checker(ast.NodeVisitor):
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.env: dict[str, _Value] = {}
        self.issues: list[Issue] = []
        self.counts = {"imports": 0, "calls": 0, "attributes": 0}
        self._call_options: dict[int, list[tuple[str, Issue]]] = {}  # constructor call -> option warnings
        self._pending: dict[tuple[str, str], list[Issue]] = {}  # (variable, option) -> warnings

    # ------------------------------------------------------------ helpers
    def _add(self, node: ast.AST, severity: str, code: str, message: str, close: list[str] | None = None) -> Issue:
        issue = Issue(getattr(node, "lineno", 0), getattr(node, "col_offset", 0), severity, code, message,
                      tuple(close or ()))
        self.issues.append(issue)
        return issue

    def _resolve(self, path: str) -> dict | None:
        return self.catalog.resolve(path)

    def _eval(self, node: ast.AST) -> _Value | None:
        """What an expression is, as far as the check follows it (no issues are reported here)."""
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._eval(node.value)
            if base is not None and base.kind == "path":
                return _Value("path", f"{base.name}.{node.attr}")
            return None
        if isinstance(node, ast.Call):
            func = self._eval(node.func)
            if func is not None and func.kind == "path":
                found = self._resolve(func.name)
                if found and found["kind"] == "class":
                    return _Value("instance", found["name"])
                if found and found["kind"] == "keyword_class":
                    return _Value("keyword", found["name"])
        return None

    def _members(self, value: _Value) -> tuple[dict, dict, bool, str]:
        """(members, fields, complete, label) of an instance or keyword object."""
        if value.kind == "keyword":
            api = self.catalog.keyword_api(value.name)
            label = f"{value.name} ({api['keyword']})" if api["keyword"] else value.name
            return api["members"], api["fields"], api["complete"], label
        members, complete = self.catalog.class_members(value.name)
        return members, {}, complete, _short(value.name)

    def _unbind(self, target: ast.AST) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                self._release(node.id)
                self.env.pop(node.id, None)

    def _release(self, var: str) -> None:
        """A variable gets a new value: its open option warnings stay as they are."""
        for key in [k for k in self._pending if k[0] == var]:
            self.issues.extend(self._pending.pop(key))

    def _activate(self, var: str | None, option: str | None) -> None:
        if var is not None and option is not None:
            self._pending.pop((var, option), None)

    @staticmethod
    def _var(node: ast.AST) -> str | None:
        return node.id if isinstance(node, ast.Name) else None

    def finish(self) -> list[Issue]:
        for warnings in self._call_options.values():
            self.issues.extend(issue for _, issue in warnings)
        for warnings in self._pending.values():
            self.issues.extend(warnings)
        self._call_options.clear()
        self._pending.clear()
        return sorted(self.issues, key=lambda i: (i.line, i.column, i.code))

    # ------------------------------------------------------------ imports
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self._release(bound)
            self.env.pop(bound, None)
            if not _related(alias.name):
                continue
            self.counts["imports"] += 1
            found = self._resolve(alias.name)
            if found is None or found["kind"] not in ("module", "keyword_namespace"):
                parent = alias.name.rpartition(".")[0]
                self._add(node, ERROR, "unknown-import",
                          f"module {alias.name} is not in {DISTRIBUTION} {self.catalog.version}",
                          self.catalog.close_names(_short(alias.name), self.catalog.module_members(parent)))
                continue
            self.env[bound] = _Value("path", alias.name if alias.asname else bound)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        names = [a for a in node.names if a.name != "*"]
        for alias in names:
            bound = alias.asname or alias.name
            self._release(bound)
            self.env.pop(bound, None)
        if node.level or not _related(module):
            return
        self.counts["imports"] += 1
        found = self._resolve(module)
        if found is None or found["kind"] not in ("module", "keyword_namespace"):
            parent = module.rpartition(".")[0]
            self._add(node, ERROR, "unknown-import", f"module {module} is not in {DISTRIBUTION} {self.catalog.version}",
                      self.catalog.close_names(_short(module), self.catalog.module_members(parent)))
            return
        for alias in names:
            path = f"{module}.{alias.name}"
            if self._resolve(path) is None:
                self._add(node, ERROR, "unknown-import",
                          f"{alias.name} is not in {module} ({DISTRIBUTION} {self.catalog.version})",
                          self.catalog.close_names(alias.name, self.catalog.module_members(module)))
                continue
            self.env[alias.asname or alias.name] = _Value("path", path)

    # ------------------------------------------------------------ bindings
    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        value = self._eval(node.value)
        for target in node.targets:
            self._bind(target, value, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._bind(node.target, self._eval(node.value) if node.value is not None else None, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Attribute):
            self._check_get(node.target)  # deck += kw keeps deck what it was

    def _bind(self, target: ast.AST, value: _Value | None, source: ast.AST | None) -> None:
        if isinstance(target, ast.Name):
            self._release(target.id)
            if value is None:
                self.env.pop(target.id, None)
                return
            self.env[target.id] = value
            warnings = self._call_options.pop(id(source), None) if source is not None else None
            for option, issue in warnings or []:
                self._pending.setdefault((target.id, option), []).append(issue)
        elif isinstance(target, ast.Attribute):
            self._check_set(target)
        else:
            if isinstance(target, ast.Subscript):
                self.visit(target)
            self._unbind(target)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):  # for / with / del targets ...
            self._release(node.id)
            self.env.pop(node.id, None)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            self._release(arg.arg)
            self.env.pop(arg.arg, None)
        self.generic_visit(node)
        self._release(node.name)
        self.env.pop(node.name, None)

    visit_AsyncFunctionDef = visit_FunctionDef

    # ------------------------------------------------------------ attributes
    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_get(node)

    def _check_path(self, node: ast.Attribute, base: _Value) -> None:
        path = f"{base.name}.{node.attr}"
        if self._resolve(path) is not None:
            return
        parent = self._resolve(base.name)
        if parent is None:
            return
        self.counts["attributes"] += 1
        if parent["kind"] in ("module", "keyword_namespace"):
            what = "keyword class" if parent["kind"] == "keyword_namespace" else "name"
            self._add(node, ERROR, "unknown-name",
                      f"{what} {node.attr} is not in {base.name} ({DISTRIBUTION} {self.catalog.version})",
                      self.catalog.close_names(node.attr, self.catalog.module_members(parent["name"])))
        elif parent["kind"] == "class":
            members, complete = self.catalog.class_members(parent["name"])
            if complete:
                self._add(node, ERROR, "unknown-member", f"{_short(parent['name'])} has no attribute {node.attr}",
                          self.catalog.close_names(node.attr, [m for m in members if m != "__init__"]))

    def _check_get(self, node: ast.Attribute) -> None:
        self.visit(node.value)
        owner = self._eval(node.value)
        if owner is None or node.attr.startswith("_"):
            return
        if owner.kind == "path":
            self._check_path(node, owner)
            return
        self.counts["attributes"] += 1
        members, fields, complete, label = self._members(owner)
        if node.attr not in members and node.attr not in fields and complete:
            self._add(node, ERROR, "unknown-member", f"{label} has no attribute {node.attr}",
                      self.catalog.close_names(node.attr, [*members, *fields]))

    def _check_set(self, target: ast.Attribute) -> None:
        # obj.options["TITLE"].active = True switches an option on
        if (target.attr == "active" and isinstance(target.value, ast.Subscript)
                and isinstance(target.value.value, ast.Attribute) and target.value.value.attr == "options"
                and isinstance(target.value.slice, ast.Constant) and isinstance(target.value.slice.value, str)):
            self._activate(self._var(target.value.value.value), target.value.slice.value)
            self.visit(target.value)
            return
        self.visit(target.value)
        owner = self._eval(target.value)
        if owner is None or owner.kind == "path" or target.attr.startswith("_"):
            return
        self.counts["attributes"] += 1
        members, fields, complete, label = self._members(owner)
        member = members.get(target.attr)
        if member is not None:
            if member.get("kind") == "property" and not member.get("settable"):
                self._add(target, ERROR, "read-only", f"{label}.{target.attr} is a read-only property")
            elif member.get("kind") in ("method", "staticmethod", "classmethod"):
                self._add(target, WARNING, "replaces-method", f"{label}.{target.attr} is a method; this replaces it")
            if owner.kind == "keyword" and target.attr in fields:
                self._activate(self._var(target.value), fields[target.attr])  # setting the property switches it on
            return
        if target.attr in fields:
            return
        if complete:
            if owner.kind == "keyword":
                self._add(target, WARNING, "unknown-field",
                          f"{target.attr} is not a field of {label}: Python keeps it on the object, "
                          "but it is not written to the deck", self.catalog.close_names(target.attr, [*fields, *members]))
            else:
                self._add(target, WARNING, "unknown-attribute",
                          f"{label} has no attribute {target.attr}; this adds a new one",
                          self.catalog.close_names(target.attr, list(members)))

    # ------------------------------------------------------------ calls
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        owner = None
        if isinstance(func, ast.Attribute):
            self.visit(func.value)
            owner = self._eval(func.value)
            if owner is not None and owner.kind == "path":
                self._check_path(func, owner)
        else:
            self.visit(func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)
        if isinstance(func, ast.Attribute) and owner is not None and owner.kind != "path":
            self._check_method_call(node, func, owner)
            return
        value = self._eval(func)
        if value is None or value.kind != "path":
            return
        found = self._resolve(value.name)
        if found is None:
            return
        if found["kind"] == "keyword_class":
            self._check_keyword_call(node, found["name"])
        elif found["kind"] == "class":
            members, _ = self.catalog.class_members(found["name"])
            init = members.get("__init__")
            if init is not None:
                self._check_arguments(node, init["parameters"], f"{_short(found['name'])}()")
        elif found["kind"] == "function":
            record = self.catalog.lookup(found["name"])
            self._check_arguments(node, record["parameters"], f"{_short(found['name'])}()")
        elif found["kind"] == "member":  # Deck.from_text(...): static and class methods only
            members, _ = self.catalog.class_members(found["class"])
            member = members.get(_short(found["name"]), {})
            if member.get("kind") in ("staticmethod", "classmethod") and "parameters" in member:
                self._check_arguments(node, member["parameters"],
                                      f"{_short(found['class'])}.{_short(found['name'])}()")

    def _check_method_call(self, node: ast.Call, func: ast.Attribute, owner: _Value) -> None:
        if func.attr.startswith("_"):
            return
        self.counts["calls"] += 1
        members, fields, complete, label = self._members(owner)
        member = members.get(func.attr)
        if member is None:
            if func.attr not in fields and complete:
                self._add(func, ERROR, "unknown-member", f"{label} has no method {func.attr}",
                          self.catalog.close_names(func.attr, [m for m in members if m != "__init__"]))
            return
        if "parameters" in member:
            self._check_arguments(node, member["parameters"], f"{label}.{func.attr}()")
        if owner.kind == "keyword" and func.attr == "activate_option" and node.args:
            option = node.args[0]
            if isinstance(option, ast.Constant) and isinstance(option.value, str):
                api = self.catalog.keyword_api(owner.name)
                if api["options"] and option.value not in api["options"]:
                    self._add(option, WARNING, "unknown-option",
                              f"{option.value} is not an option of {label} (options: {', '.join(api['options'])}); "
                              "PyDyna accepts it without an error, but no option card is switched on",
                              self.catalog.close_names(option.value, api["options"]))
                self._activate(self._var(func.value), option.value)

    def _check_keyword_call(self, node: ast.Call, class_name: str) -> None:
        self.counts["calls"] += 1
        api = self.catalog.keyword_api(class_name)
        label = f"keywords.{class_name}()" + (f" ({api['keyword']})" if api["keyword"] else "")
        if node.args:
            self._add(node, ERROR, "positional-argument",
                      f"{label} takes field names as keyword arguments only; {len(node.args)} positional "
                      "argument(s) given")
        fields = api["fields"]
        options: list[tuple[str, Issue]] = []
        for keyword in node.keywords:
            if keyword.arg is None or keyword.arg in GENERIC_KEYWORD_ARGS:
                continue
            if keyword.arg not in fields:
                self._add(keyword.value, WARNING, "unknown-field",
                          f"{keyword.arg} is not a field of {label}: PyDyna ignores it without an error",
                          self.catalog.close_names(keyword.arg, list(fields)))
            elif fields[keyword.arg]:
                option = fields[keyword.arg]
                issue = Issue(keyword.value.lineno, keyword.value.col_offset, WARNING, "option-not-active",
                              f"{keyword.arg} is on the {option} option card of {label}: as a constructor "
                              f"argument it does not switch {option} on, so the card is not written. Call "
                              f".activate_option(\"{option}\") or set .{keyword.arg} after creating the keyword")
                options.append((option, issue))
        if options:
            self._call_options[id(node)] = options

    def _check_arguments(self, node: ast.Call, parameters: list[dict], label: str) -> None:
        self.counts["calls"] += 1
        positional = [p for p in parameters if not p["name"].startswith("*") and not p.get("keyword_only")]
        named = {p["name"] for p in parameters if not p["name"].startswith("*")}
        var_args = any(p["name"].startswith("*") and not p["name"].startswith("**") for p in parameters)
        var_kwargs = any(p["name"].startswith("**") for p in parameters)
        star_args = any(isinstance(a, ast.Starred) for a in node.args)
        star_kwargs = any(k.arg is None for k in node.keywords)
        given = {k.arg for k in node.keywords if k.arg}
        if not star_args and not var_args and len(node.args) > len(positional):
            self._add(node, ERROR, "too-many-arguments",
                      f"{label} takes {len(positional)} positional argument(s), {len(node.args)} given")
        if not var_kwargs:
            for keyword in node.keywords:
                if keyword.arg and keyword.arg not in named:
                    self._add(keyword.value, ERROR, "unknown-argument", f"{label} has no parameter {keyword.arg}",
                              self.catalog.close_names(keyword.arg, sorted(named)))
        if star_args or star_kwargs:
            return
        covered = {p["name"] for p in positional[:len(node.args)]} | given
        missing = [p["name"] for p in parameters
                   if p.get("required") and not p["name"].startswith("*") and p["name"] not in covered]
        if missing:
            self._add(node, ERROR, "missing-argument", f"{label} is missing required argument(s): {', '.join(missing)}")


def check_source(catalog: Catalog, text: str, name: str = "<script>") -> dict:
    """Check one script; the result lists every issue with its line (1-based) and column (0-based)."""
    base = {"package": DISTRIBUTION, "version": catalog.version, "script": name}
    try:
        tree = ast.parse(text, filename=name)
    except SyntaxError as exc:
        issue = Issue(exc.lineno or 0, max((exc.offset or 1) - 1, 0), ERROR, "syntax", exc.msg)
        return {**base, "ok": False, "errors": 1, "warnings": 0, "issues": [issue.as_dict()],
                "checked": {"imports": 0, "calls": 0, "attributes": 0}, "note": "The script does not parse."}
    checker = _Checker(catalog)
    try:
        checker.visit(tree)
    except CatalogError as exc:  # a keyword file of the package that cannot be read
        checker.issues.append(Issue(0, 0, ERROR, "catalog", str(exc)))
    except RecursionError:
        checker.issues.append(Issue(0, 0, ERROR, "too-deep", "the script is nested too deeply to check"))
    issues = checker.finish()
    errors = sum(1 for i in issues if i.severity == ERROR)
    note = NOTE if checker.counts["imports"] else "The script does not import ansys.dyna.core: nothing to check."
    return {**base, "ok": not issues, "errors": errors, "warnings": len(issues) - errors,
            "issues": [i.as_dict() for i in issues], "checked": dict(checker.counts), "note": note}
