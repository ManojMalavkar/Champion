"""Tool factory of the knowledge layer (M1).

``make_search_tool`` and ``make_lookup_tool`` build ``FunctionTool``s for one declared
collection. Every tool:

* gets a tool-policy entry with right ``none``: it reads only Champion's own knowledge
  root from the config (never a path from the model), so it also works at levels without
  read;
* runs through ``run_guarded`` (audit row, output cap ``execution.max_output_chars``);
* returns ``ERROR: ...`` with what to do when the database, packages or model are missing.

``filters`` maps an argument the model may give to a metadata key, for example
``{"category": "category"}``; empty or null arguments are not used. Results show raw
distance, score, source and chunk; each text is cut to ``knowledge.max_hit_chars``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from agents import FunctionTool

from champion.knowledge.root import KnowledgeError
from champion.knowledge.search import format_hits, lookup, search
from champion.policy.tool_policy import ToolRule, register
from champion.tools.guard import ToolError, run_guarded


def _filter_properties(filters: Mapping[str, str]) -> dict[str, Any]:
    return {arg: {"type": ["string", "null"], "description": f"only entries whose {key} is exactly this; null = any"}
            for arg, key in filters.items()}


def _filter_values(args: Mapping[str, Any], filters: Mapping[str, str]) -> dict[str, Any]:
    return {key: args[arg] for arg, key in filters.items() if args.get(arg) not in (None, "")}


def _arguments(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise ToolError("the arguments are not valid JSON") from None
    if not isinstance(value, dict):
        raise ToolError("the arguments must be an object")
    return value


def _run(work):
    try:
        return work()
    except KnowledgeError as exc:
        raise ToolError(str(exc)) from None


def make_search_tool(*, name: str, agent: str, collection: str, description: str,
                     filters: Mapping[str, str] | None = None, default_results: int = 8) -> FunctionTool:
    """A tool ``name(query, n_results, <filters>)`` that searches ``agent``/``collection``."""
    filters = dict(filters or {})
    register(ToolRule(name, "none", f"Search the {agent} knowledge base ({collection}); reads only Champion's "
                                    "knowledge root"))
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to search for, in words"},
            "n_results": {"type": ["integer", "null"],
                          "description": f"number of results (default {default_results})"},
            **_filter_properties(filters),
        },
        "required": ["query", "n_results", *filters],
        "additionalProperties": False,
    }

    async def invoke(ctx, raw: str) -> str:
        try:
            args = _arguments(raw)
        except ToolError as exc:
            return f"ERROR: {exc}"
        config = ctx.context.config
        values = _filter_values(args, filters)

        def work() -> str:
            hits = _run(lambda: search(config, agent, collection, str(args.get("query") or ""),
                                       args.get("n_results") or default_results, values))
            return format_hits(hits, int(config.get("knowledge.max_hit_chars", 1500)), f"{agent}/{collection}")

        return await run_guarded(ctx, name, args, work)

    return FunctionTool(name=name, description=description, params_json_schema=schema, on_invoke_tool=invoke,
                        strict_json_schema=True)


def make_lookup_tool(*, name: str, agent: str, collection: str, description: str, key_arg: str, key_field: str,
                     filters: Mapping[str, str] | None = None, limit: int = 5) -> FunctionTool:
    """A tool ``name(<key_arg>, <filters>)`` that returns entries whose ``key_field`` equals the value."""
    filters = dict(filters or {})
    if key_arg in filters:
        raise ValueError(f"{name}: {key_arg} is both the key and a filter")
    register(ToolRule(name, "none", f"Exact lookup in the {agent} knowledge base ({collection}); reads only "
                                    "Champion's knowledge root"))
    schema = {
        "type": "object",
        "properties": {key_arg: {"type": "string", "description": f"exact {key_field}"}, **_filter_properties(filters)},
        "required": [key_arg, *filters],
        "additionalProperties": False,
    }

    async def invoke(ctx, raw: str) -> str:
        try:
            args = _arguments(raw)
        except ToolError as exc:
            return f"ERROR: {exc}"
        config = ctx.context.config
        values = {key_field: args.get(key_arg), **_filter_values(args, filters)}

        def work() -> str:
            if not str(values.get(key_field) or "").strip():
                raise ToolError(f"{key_arg} is empty")
            hits = _run(lambda: lookup(config, agent, collection, filters=values, limit=limit))
            return format_hits(hits, int(config.get("knowledge.max_hit_chars", 1500)), f"{agent}/{collection}")

        return await run_guarded(ctx, name, args, work)

    return FunctionTool(name=name, description=description, params_json_schema=schema, on_invoke_tool=invoke,
                        strict_json_schema=True)
