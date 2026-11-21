"""PyDyna tools (track T1): the tools of the old PyDyna_Agent on Champion's tool guard.

=========================  ==================  ==================================
Tool                       Sub-agent           Data
=========================  ==================  ==================================
lookup_keyword             Keyword, Code       keyword JSONL (records.py, merged)
validate_keyword_params    Keyword             keyword JSONL
search_keyword             Keyword             ``lsdyna_keywords`` (+ category)
search_pydyna_api          Code                ``pydyna_api``
search_pydpf_post          DPF-Post            ``pydpf_post``
=========================  ==================  ==================================

``get_material_model`` and ``get_contact_type`` of the old agent only put
"material model" / "contact algorithm" in front of the query (user decision
21 Nov 2026): here ``search_keyword`` has a real ``category`` filter instead
(``material``, ``contact``, ...).

Every tool has a policy entry with right ``none``: it reads only Champion's own
PyDyna knowledge base (paths from the config), never the user's files, and
sends nothing out. Results are JSON text with sources (keyword record
``sources``, ``source_path`` and ``chunk_index`` for code chunks), the raw
``distance`` and the old ``score`` (1 - distance).

Paths: ``pydyna.database_path`` (default ``<project>/knowledge/pydyna/vector_db``)
and the folder of ``pydyna.keyword_reference_path`` (default
``<project>/knowledge/pydyna/knowledge-base``).
"""

from __future__ import annotations

import difflib
import json
import threading
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from champion.core.config import Config
from champion.core.context import ChampionContext
from champion.policy.tool_policy import ToolRule, register
from champion.specialists.pydyna import records as rec
from champion.specialists.pydyna.search import SearchBackend, SearchUnavailable, backend_for
from champion.tools.guard import ToolError, run_guarded

MAX_RESULTS = 20
KEYWORD_TEXT_CHARS = 300  # as the old search_keyword
CODE_TEXT_CHARS = 1800  # as the old search_pydyna_api / search_pydpf_post

for _rule in (
    ToolRule("lookup_keyword", "none", "Full record of one LS-DYNA keyword from the PyDyna knowledge base"),
    ToolRule("validate_keyword_params", "none", "Check keyword card parameters against the PyDyna knowledge base"),
    ToolRule("search_keyword", "none", "Search LS-DYNA keywords in the PyDyna knowledge base"),
    ToolRule("search_pydyna_api", "none", "Search the PyDyna (ansys-dyna-core) API and source"),
    ToolRule("search_pydpf_post", "none", "Search the PyDPF-Post API, source and examples"),
):
    register(_rule)


# ---------------------------------------------------------------- paths and data

def database_path(config: Config) -> Path:
    value = config.get("pydyna.database_path")
    return Path(value) if value else Path(config.root) / "knowledge" / "pydyna" / "vector_db"


def knowledge_base_path(config: Config) -> Path:
    value = config.get("pydyna.keyword_reference_path")
    return Path(value).parent if value else Path(config.root) / "knowledge" / "pydyna" / "knowledge-base"


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


def _check_n(n_results: int) -> int:
    if isinstance(n_results, bool) or not isinstance(n_results, int) or not 1 <= n_results <= MAX_RESULTS:
        raise ToolError(f"n_results must be a whole number from 1 to {MAX_RESULTS}")
    return n_results


def _query(backend: SearchBackend, collection: str, text: str, n: int, where: dict | None = None) -> list:
    if not str(text).strip():
        raise ToolError("query is empty")
    try:
        return backend.query(collection, text, n, where)
    except SearchUnavailable as exc:
        raise ToolError(str(exc)) from None


# ---------------------------------------------------------------- the work (testable without the SDK)

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


def search_keywords(config: Config, backend: SearchBackend, query: str, category: str | None = None,
                    n_results: int = 8) -> str:
    n = _check_n(n_results)
    where = None
    if category:
        category = category.strip().lower()
        records, _ = keywords(config)
        known = sorted({r.get("category", "") for r in records.values()} - {""})
        if category not in known:
            raise ToolError(f"unknown category {category}; known: {', '.join(known)}")
        where = {"category": category}
    hits = _query(backend, rec.KW_COLLECTION, query, n, where)
    return _json([{"keyword": h.metadata.get("keyword", ""), "category": h.metadata.get("category", ""),
                   "score": h.score, "distance": round(h.distance, 4),
                   "text": h.document[:KEYWORD_TEXT_CHARS]} for h in hits])


def _code_hits(hits: list) -> list[dict]:
    return [{"class": h.metadata.get("class", ""), "method": h.metadata.get("method", ""),
             "source_path": h.metadata.get("source_path", ""), "chunk_index": h.metadata.get("chunk_index", -1),
             "score": h.score, "distance": round(h.distance, 4), "text": h.document[:CODE_TEXT_CHARS]}
            for h in hits]


def search_api(config: Config, backend: SearchBackend, query: str, n_results: int = 8) -> str:
    hits = _query(backend, rec.API_COLLECTION, query, _check_n(n_results))
    return _json(_code_hits(hits))


def search_dpf_post(config: Config, backend: SearchBackend, query: str, n_results: int = 8) -> str:
    """The existing database still holds repository files; they are dropped here too (T1.0 filter)."""
    n = _check_n(n_results)
    hits = _query(backend, rec.DPF_POST_COLLECTION, query, min(2 * n, 2 * MAX_RESULTS))
    kept = [h for h in hits if rec.keep_dpf_post_path(h.metadata.get("source_path", ""))]
    return _json(_code_hits(kept[:n]))


def _backend(context: ChampionContext) -> SearchBackend:
    return backend_for(database_path(context.config))


# ---------------------------------------------------------------- tools

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
async def search_keyword(ctx: RunContextWrapper[ChampionContext], query: str, category: str | None = None,
                         n_results: int = 8) -> str:
    """Search LS-DYNA keywords by meaning. Use category to search only one family, for example
    "material" for material model choice or "contact" for contact algorithm choice.

    Args:
        query: What the keyword is for, for example "piecewise linear plasticity for steel".
        category: Optional family: material, contact, section, control, output, element, load, boundary, part, node, definition, or another prefix in lower case (airbag, ...).
        n_results: Number of results (1-20, default 8).
    """
    arguments = {"query": query, "category": category, "n_results": n_results}
    return await run_guarded(ctx, "search_keyword", arguments,
                             lambda: search_keywords(ctx.context.config, _backend(ctx.context), query,
                                                     category, n_results))


@function_tool
async def search_pydyna_api(ctx: RunContextWrapper[ChampionContext], query: str, n_results: int = 8) -> str:
    """Search the PyDyna (ansys-dyna-core) Python API docs and source code. Results name the
    source file and chunk.

    Args:
        query: What the code should do, for example "create a deck with a shell part".
        n_results: Number of results (1-20, default 8).
    """
    arguments = {"query": query, "n_results": n_results}
    return await run_guarded(ctx, "search_pydyna_api", arguments,
                             lambda: search_api(ctx.context.config, _backend(ctx.context), query, n_results))


@function_tool
async def search_pydpf_post(ctx: RunContextWrapper[ChampionContext], query: str, n_results: int = 8) -> str:
    """Search Ansys PyDPF-Post (post-processing of d3plot / binout results) source, docs and
    examples. Results name the source file and chunk.

    Args:
        query: What to post-process, for example "internal energy over time from d3plot".
        n_results: Number of results (1-20, default 8).
    """
    arguments = {"query": query, "n_results": n_results}
    return await run_guarded(ctx, "search_pydpf_post", arguments,
                             lambda: search_dpf_post(ctx.context.config, _backend(ctx.context), query, n_results))


KEYWORD_TOOLS = (lookup_keyword, search_keyword, validate_keyword_params)
CODE_TOOLS = (search_pydyna_api, lookup_keyword)
DPF_POST_TOOLS = (search_pydpf_post,)
