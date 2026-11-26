"""PyDyna tools (track T1): the tools of the old PyDyna_Agent on Champion's tool guard.

=========================  ==================  ==================================
Tool                       Sub-agent           Data
=========================  ==================  ==================================
lookup_api                 Keyword, Code       installed ansys-dyna-core (api_catalog.py)
check_pydyna_script        Code                a script (file or text) + installed package (api_check.py)
lookup_dpf_api             DPF-Post, DPF-Core  installed ansys-dpf-post + ansys-dpf-core (dpf_catalog.py)
find_dpf_operators         DPF-Post, DPF-Core  DPF operators of the installed ansys-dpf-core
check_dpf_script           DPF-Post, DPF-Core  a script (file or text) + installed DPF packages
lookup_keyword             Keyword, Code       keyword JSONL (records.py, merged)
validate_keyword_params    Keyword             keyword JSONL
search_keyword             Keyword             ``lsdyna_keywords`` (+ category)
search_pydyna_api          Code                ``pydyna_api``
search_pydpf_post          DPF-Post            ``pydpf_post``
search_pydpf_core          DPF-Core            ``pydpf_core`` in its own database folder
=========================  ==================  ==================================

``get_material_model`` and ``get_contact_type`` of the old agent only put
"material model" / "contact algorithm" in front of the query (user decision
21 Nov 2026): here ``search_keyword`` has a real ``category`` filter instead
(``material``, ``contact``, ...).

``check_pydyna_script`` and ``check_dpf_script`` check script text at every access level; given a path, it reads
that one ``.py`` file only through the path policy with right ``read`` (``check_path``, as
``code_check``: blocked at a level without read, sensitive files and symbolic links
refused). It never runs or imports the script. Every tool has a policy entry with right
``none``; apart from that one file, a tool reads only Champion's own
PyDyna knowledge base (paths from the config) or the files of the ansys-dyna-core
package installed in Champion's Python (``lookup_api``, parsed, never imported or
run), never the user's files, and sends nothing out. Lookups and checks return JSON text
with sources (keyword record ``sources``, package version and source file); the four
search tools return M1's text (id, score = 1 - distance, raw distance, source, chunk).

T1.1 (M1): the four search tools are built by ``champion.knowledge.tools.make_search_tool``
on the collections declared in ``KNOWLEDGE`` (same tool names as before); M1 opens the
stores read-only, embeds offline and checks manifests. Paths: ``pydyna.knowledge_root``
(default ``<project>/knowledge/pydyna``) with ``knowledge-base`` and, since CR-4, Champion's
own stores ``index`` (``lsdyna_keywords``, ``pydyna_api``, ``pydpf_post``) and
``index_dpf_core`` (``pydpf_core``). All four are built from the knowledge-base JSONL with
``champion kb build --agent pydyna`` (records from ``records.py``). The old Chroma folders
``vector_db`` and ``vector_db_dpf_core`` stay as backups and are never opened. The old keys
``pydyna.database_path``, ``pydyna.dpf_core_database_path`` and
``pydyna.keyword_reference_path`` still work for the keyword lookups; for a search they must
match the root's layout.
"""

from __future__ import annotations

import difflib
import json
import os
import stat
import threading
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from champion.core.config import Config
from champion.core.context import ChampionContext
from champion.knowledge.registry import Document, KnowledgeCollection, KnowledgeSpec, register_knowledge
from champion.knowledge.root import KnowledgeError, resolve_root
from champion.knowledge.tools import make_search_tool
from champion.policy.scope import check_path, open_no_symlinks
from champion.policy.tool_policy import ToolRule, register
from champion.specialists.pydyna import api_catalog, api_check, dpf_catalog
from champion.specialists.pydyna import records as rec
from champion.tools.guard import ToolError, run_guarded

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
STORE = "index"  # lsdyna_keywords, pydyna_api, pydpf_post (CR-4)
DPF_CORE_STORE = "index_dpf_core"  # pydpf_core in its own store folder (user, 24 Nov 2026)


def _m1(documents):
    """``records.py`` documents as M1 ``Document``s (M1 builds the collection from them)."""
    def build(kb_dir):
        return [Document(d.id, d.text, d.metadata) for d in documents(kb_dir)]
    return build


_keyword_documents = _m1(rec.keyword_documents)
_api_documents = _m1(rec.api_documents)
_post_documents = _m1(rec.dpf_post_documents)
_core_documents = _m1(rec.dpf_core_documents)

KNOWLEDGE = register_knowledge(KnowledgeSpec("pydyna", (
    KnowledgeCollection(rec.KW_COLLECTION, EMBED_MODEL, database=STORE,
                        documents=_keyword_documents, files=rec.keyword_files),
    KnowledgeCollection(rec.API_COLLECTION, EMBED_MODEL, database=STORE,
                        documents=_api_documents, files=rec.api_files,
                        keep=lambda hit: rec.keep_api_path(hit.source)),
    KnowledgeCollection(rec.DPF_POST_COLLECTION, EMBED_MODEL, database=STORE,
                        documents=_post_documents, files=rec.dpf_post_files,
                        keep=lambda hit: rec.keep_code_path(hit.source)),
    KnowledgeCollection(rec.DPF_CORE_COLLECTION, EMBED_MODEL, database=DPF_CORE_STORE,
                        documents=_core_documents, files=rec.dpf_core_files,
                        keep=lambda hit: rec.keep_code_path(hit.source)),
), config_section="pydyna"))

MAX_SCRIPT_BYTES = 1024 * 1024

for _rule in (
    ToolRule("lookup_api", "none", "Exact PyDyna (ansys-dyna-core) API record from the installed package"),
    # right none: code text needs no access; a path goes through check_path(..., "read")
    ToolRule("check_pydyna_script", "none",
             "Check a PyDyna script against the installed ansys-dyna-core API (nothing is run)"),
    ToolRule("lookup_dpf_api", "none", "Exact PyDPF-Post / PyDPF-Core API or DPF operator record from the installed packages"),
    ToolRule("find_dpf_operators", "none", "Find DPF operators of the installed ansys-dpf-core by words"),
    # right none: code text needs no access; a path goes through check_path(..., "read")
    ToolRule("check_dpf_script", "none",
             "Check a PyDPF script against the installed ansys-dpf-post / ansys-dpf-core API (nothing is run)"),
    ToolRule("lookup_keyword", "none", "Full record of one LS-DYNA keyword from the PyDyna knowledge base"),
    ToolRule("validate_keyword_params", "none", "Check keyword card parameters against the PyDyna knowledge base"),
):
    register(_rule)


# ---------------------------------------------------------------- paths and data

def database_path(config: Config) -> Path:
    value = config.get("pydyna.database_path")
    return Path(value) if value else resolve_root(config, "pydyna").db_dir(STORE)


def dpf_core_database_path(config: Config) -> Path:
    """The ``pydpf_core`` store: its own folder next to the main one."""
    value = config.get("pydyna.dpf_core_database_path")
    return Path(value) if value else database_path(config).parent / DPF_CORE_STORE


def knowledge_base_path(config: Config) -> Path:
    value = config.get("pydyna.keyword_reference_path")
    return Path(value).parent if value else resolve_root(config, "pydyna").kb_dir


_KEYWORDS: dict[Path, tuple[tuple, dict, dict]] = {}
_KEYWORDS_LOCK = threading.Lock()


def _signature(kb_dir: Path) -> tuple:
    files = [*rec.keyword_schema_files(kb_dir), kb_dir / rec.KW_REF_FILE]
    return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files if p.is_file())


def keywords(config: Config) -> tuple[dict, dict]:
    """``(records, index)`` of the knowledge base, read again only when a keyword file changed."""
    kb_dir = knowledge_base_path(config).resolve()
    signature = _signature(kb_dir)
    if not signature:
        raise ToolError(f"no keyword files in {kb_dir} (keyword_schema.jsonl, keyword_reference.jsonl); "
                        "set pydyna.keyword_reference_path in the config")
    with _KEYWORDS_LOCK:
        cached = _KEYWORDS.get(kb_dir)
        if cached is None or cached[0] != signature:
            try:
                records = rec.load_keywords(kb_dir)
            except ValueError as exc:
                raise ToolError(str(exc)) from None
            cached = (signature, records, rec.keyword_index(records))
            _KEYWORDS[kb_dir] = cached
        return cached[1], cached[2]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- the work (testable without the SDK)

def api_record(catalog: api_catalog.Catalog, name: str) -> str:
    try:
        return _json(catalog.lookup(name))
    except api_catalog.CatalogError as exc:
        raise ToolError(str(exc)) from None


def _read_script(context: ChampionContext, path: str) -> tuple[str, str]:
    target = check_path(context.level, context.working_directory, path, "read")
    if target.suffix.lower() != ".py":
        raise ToolError(f"{target} is not a Python file (.py)")
    try:
        fd = open_no_symlinks(target, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ToolError(f"{target} does not exist") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ToolError(f"{target} is not a regular file")
        if info.st_size > MAX_SCRIPT_BYTES:
            raise ToolError(f"{target} is larger than {MAX_SCRIPT_BYTES // 1024} KB")
        data = stream.read(MAX_SCRIPT_BYTES + 1)
    try:
        return data.decode("utf-8"), str(target)
    except UnicodeDecodeError as exc:
        raise ToolError(f"{target} is not UTF-8 (byte {exc.start})") from None


def script_check(context: ChampionContext, catalog, path: str | None = None, code: str | None = None) -> str:
    """JSON result of the API check of one script (PyDyna or DPF catalog), given as a file path or as text."""
    if (path is None) == (code is None):
        raise ToolError("give either path (a .py file in the working folder) or code (the script text), not both")
    if code is not None:
        if len(code.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise ToolError(f"code is larger than {MAX_SCRIPT_BYTES // 1024} KB")
        text, name = code, "<code>"
    else:
        text, name = _read_script(context, path)
    return _json(api_check.check_source(catalog, text, name))


def installed_catalog() -> api_catalog.Catalog:
    try:
        return api_catalog.catalog_for()
    except api_catalog.CatalogError as exc:
        raise ToolError(str(exc)) from None


def installed_dpf_catalog() -> dpf_catalog.DpfCatalog:
    try:
        return dpf_catalog.dpf_catalog_for()
    except api_catalog.CatalogError as exc:
        raise ToolError(str(exc)) from None


def dpf_operators(catalog: dpf_catalog.DpfCatalog, query: str, lsdyna: bool = False, category: str | None = None,
                  n_results: int = 10) -> str:
    if isinstance(n_results, bool) or not isinstance(n_results, int) or not 1 <= n_results <= dpf_catalog.MAX_OPERATORS:
        raise ToolError(f"n_results must be a whole number from 1 to {dpf_catalog.MAX_OPERATORS}")
    try:
        hits = catalog.find_operators(query, lsdyna, category or None, n_results)
    except api_catalog.CatalogError as exc:
        raise ToolError(str(exc)) from None
    return _json({"package": dpf_catalog.CORE_DISTRIBUTION, "version": catalog.core.version, "operators": hits,
                  "note": "Exact word match on operator names and descriptions; lookup_dpf_api gives the pins."})


def lookup(config: Config, keyword_name: str) -> str:
    records, index = keywords(config)
    name = rec.normalize_keyword(keyword_name)
    record = index.get(name)
    if record is None:
        close = difflib.get_close_matches(name, list(records), n=5, cutoff=0.75)
        hint = f" Close names: {', '.join(close)}." if close else " Use search_keyword to find it."
        raise ToolError(f"keyword {name} is not in the PyDyna knowledge base.{hint}")
    result = {key: record[key] for key in ("keyword", "aliases", "category", "description", "parameters", "sources")
              if key in record}
    if name != record["keyword"]:
        result["found_as_alias"] = name
    return _json(result)


def validate(config: Config, keyword_name: str, parameters: str) -> str:
    try:
        values = json.loads(parameters)
    except json.JSONDecodeError as exc:
        raise ToolError(f"parameters must be a JSON object, for example {{\"RO\": 7.85e-9}} ({exc.msg})") from None
    if not isinstance(values, dict):
        raise ToolError("parameters must be a JSON object of field name -> value")
    _, index = keywords(config)
    name = rec.normalize_keyword(keyword_name)
    record = index.get(name)
    if record is None:
        raise ToolError(f"keyword {name} is not in the PyDyna knowledge base")
    return _json(rec.check_params(record, values))


def keyword_category(config: Config, category: str | None) -> str | None:
    """The category in lower case; an unknown one is an error that names the known ones (old behaviour)."""
    if category is None or not str(category).strip():
        return None
    category = str(category).strip().lower()
    records, _ = keywords(config)
    known = sorted({r.get("category", "") for r in records.values()} - {""})
    if category not in known:
        raise ToolError(f"unknown category {category}; known: {', '.join(known)}")
    return category


def check_search_paths(config: Config) -> None:
    """M1 searches ``pydyna.knowledge_root``; an old path key that points elsewhere is an error, not ignored."""
    root = resolve_root(config, "pydyna")
    expected = {"database_path": root.db_dir(STORE),
                "dpf_core_database_path": root.db_dir(DPF_CORE_STORE),
                "keyword_reference_path": root.kb_dir / rec.KW_REF_FILE}
    for key, path in expected.items():
        value = config.get(f"pydyna.{key}")
        if value and Path(value) != path:
            raise KnowledgeError(f"pydyna.{key} ({value}) does not match the M1 layout ({path}); set "
                                 "pydyna.knowledge_root and clear the old path keys")


# ---------------------------------------------------------------- tools

@function_tool
async def lookup_api(ctx: RunContextWrapper[ChampionContext], name: str) -> str:
    """Get the exact record of a PyDyna (ansys-dyna-core) name from the installed package, with its
    version and source file: a class (import line, methods with signatures, properties), a method or
    property (parameters, required or not, types, defaults, return type, docstring), or a keyword
    class (every card in order with Python field names, types, positions, widths, defaults,
    descriptions, options and curve links). Unknown names come back with close names.

    Args:
        name: For example "Deck", "Deck.import_file", "Mat024", "keywords.SectionShell", "*MAT_024" or "*MAT_024_TITLE".
    """
    return await run_guarded(ctx, "lookup_api", {"name": name}, lambda: api_record(installed_catalog(), name))


@function_tool
async def lookup_keyword(ctx: RunContextWrapper[ChampionContext], keyword_name: str) -> str:
    """Get the full record of one LS-DYNA keyword: category, description, aliases and every card
    field (type, card number, default, unit, limits, required) with its sources.

    Args:
        keyword_name: Exact keyword or alias, with or without *, for example "*MAT_024" or "MAT_PIECEWISE_LINEAR_PLASTICITY".
    """
    arguments = {"keyword_name": keyword_name}
    return await run_guarded(ctx, "lookup_keyword", arguments, lambda: lookup(ctx.context.config, keyword_name))


@function_tool
async def validate_keyword_params(ctx: RunContextWrapper[ChampionContext], keyword_name: str,
                                  parameters: str) -> str:
    """Check card parameters against an LS-DYNA keyword: missing required fields and wrong types
    are errors; unknown fields and values outside min/max are warnings.

    Args:
        keyword_name: Keyword or alias, for example "*MAT_024".
        parameters: JSON object of field name -> value, for example {"RO": 7.85e-9, "E": 210000.0, "PR": 0.3}.
    """
    arguments = {"keyword_name": keyword_name, "parameters": parameters}
    return await run_guarded(ctx, "validate_keyword_params", arguments,
                             lambda: validate(ctx.context.config, keyword_name, parameters))


@function_tool
async def check_pydyna_script(ctx: RunContextWrapper[ChampionContext], path: str | None = None,
                              code: str | None = None) -> str:
    """Check a PyDyna (ansys-dyna-core) script against the API of the installed package; nothing is run.
    Reports, with line numbers: unknown imports, keyword classes, methods and attributes; unknown,
    missing or too many arguments; positional arguments to keyword classes; read-only properties;
    names that are not fields of a keyword (PyDyna ignores them without an error); fields of an
    option card (such as TITLE) given to the constructor without switching the option on.

    Args:
        path: A .py file; a relative path starts at the working folder. Give path or code.
        code: The script text, for a script that is not saved yet. Give path or code.
    """
    arguments = {"path": path} if path is not None else {"code_chars": len(code or "")}
    return await run_guarded(ctx, "check_pydyna_script", arguments,
                             lambda: script_check(ctx.context, installed_catalog(), path, code))


@function_tool
async def lookup_dpf_api(ctx: RunContextWrapper[ChampionContext], name: str) -> str:
    """Get the exact record of a PyDPF name from the installed ansys-dpf-post and ansys-dpf-core, with
    version and source file: a DPF operator (input and output pins with types and documents, LS-DYNA or
    not, usage line), a class (import line, methods, properties), a method, property or function
    (parameters, required or not, defaults, return type, docstring). Unknown names come back with close names.

    Args:
        name: For example "part_internal_energy", "ops.result.global_kinetic_energy", "load_simulation", "Model", "DataSources.set_result_file_path" or "TransientMechanicalSimulation.displacement".
    """
    return await run_guarded(ctx, "lookup_dpf_api", {"name": name},
                             lambda: api_record(installed_dpf_catalog(), name))


@function_tool
async def find_dpf_operators(ctx: RunContextWrapper[ChampionContext], query: str, lsdyna: bool = False,
                             category: str | None = None, n_results: int = 10) -> str:
    """Find DPF operators of the installed ansys-dpf-core whose name or description has the words of
    the query (exact words, best first). Use lsdyna=true for LS-DYNA results (d3plot, binout: part
    and global energies, contact forces ...).

    Args:
        query: Words of the result, for example "internal energy part" or "contact force".
        lsdyna: Only operators for LS-DYNA results.
        category: Optional operator family: result, math, mesh, scoping, utility, ...
        n_results: Number of results (1-30, default 10).
    """
    arguments = {"query": query, "lsdyna": lsdyna, "category": category, "n_results": n_results}
    return await run_guarded(ctx, "find_dpf_operators", arguments,
                             lambda: dpf_operators(installed_dpf_catalog(), query, lsdyna, category, n_results))


@function_tool
async def check_dpf_script(ctx: RunContextWrapper[ChampionContext], path: str | None = None,
                           code: str | None = None) -> str:
    """Check a PyDPF (ansys-dpf-post / ansys-dpf-core) script against the API of the installed packages;
    nothing is run and no DPF server is needed. Reports, with line numbers: unknown imports, names,
    operators, methods and attributes; unknown, missing or too many arguments; unknown operator input
    and output pins (op.inputs.<pin>, op.outputs.<pin>); read-only properties.

    Args:
        path: A .py file; a relative path starts at the working folder. Give path or code.
        code: The script text, for a script that is not saved yet. Give path or code.
    """
    arguments = {"path": path} if path is not None else {"code_chars": len(code or "")}
    return await run_guarded(ctx, "check_dpf_script", arguments,
                             lambda: script_check(ctx.context, installed_dpf_catalog(), path, code))


# ---------------------------------------------------------------- search tools (M1, T1.1)

def _search_tool(name: str, collection: str, description: str, category: bool = False):
    """An M1 search tool; before M1 runs it: old path keys must match the root, the category is checked."""
    tool = make_search_tool(name=name, agent="pydyna", collection=collection, description=description,
                            filters={"category": "category"} if category else None)
    search = tool.on_invoke_tool

    async def invoke(ctx, raw: str) -> str:
        try:
            args = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return await search(ctx, raw)  # M1 reports bad arguments itself
        try:
            config = ctx.context.config
            check_search_paths(config)
            if category and isinstance(args, dict):
                args["category"] = keyword_category(config, args.get("category"))
                raw = json.dumps(args)
        except (KnowledgeError, ToolError) as exc:
            message = str(exc)

            def refuse():
                raise ToolError(message)

            return await run_guarded(ctx, name, args if isinstance(args, dict) else {}, refuse)  # audited
        return await search(ctx, raw)

    tool.on_invoke_tool = invoke
    return tool


search_keyword = _search_tool(
    "search_keyword", rec.KW_COLLECTION,
    "Search LS-DYNA keywords by meaning. Use category to search only one family, for example \"material\" "
    "for material model choice or \"contact\" for contact algorithm choice (others: section, control, output, "
    "element, load, boundary, part, node, definition, or another prefix in lower case such as airbag). "
    "Query: what the keyword is for, for example \"piecewise linear plasticity for steel\".", category=True)
search_pydyna_api = _search_tool(
    "search_pydyna_api", rec.API_COLLECTION,
    "Search the PyDyna (ansys-dyna-core) Python API docs, source code and examples. Results name the source "
    "file and chunk; confirm names with lookup_api. Query: what the code should do, for example "
    "\"create a deck with a shell part\".")
search_pydpf_post = _search_tool(
    "search_pydpf_post", rec.DPF_POST_COLLECTION,
    "Search Ansys PyDPF-Post (post-processing of d3plot / binout results) source, docs and examples. Results "
    "name the source file and chunk; confirm names with lookup_dpf_api. Query: what to post-process, for "
    "example \"internal energy over time from d3plot\".")
search_pydpf_core = _search_tool(
    "search_pydpf_core", rec.DPF_CORE_COLLECTION,
    "Search Ansys PyDPF-Core (DPF operators, workflows, Model, DataSources, fields containers, d3plot / binout "
    "readers) source, docs and examples. Results name the source file and chunk; confirm pins with "
    "lookup_dpf_api. Query: what to do with DPF, for example \"read part energies from a binout with operators\".")

KEYWORD_TOOLS = (lookup_keyword, search_keyword, validate_keyword_params, lookup_api)
CODE_TOOLS = (lookup_api, search_pydyna_api, lookup_keyword, check_pydyna_script)
DPF_POST_TOOLS = (search_pydpf_post, lookup_dpf_api, find_dpf_operators, check_dpf_script)
DPF_CORE_TOOLS = (search_pydpf_core, lookup_dpf_api, find_dpf_operators, check_dpf_script)
