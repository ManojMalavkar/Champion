"""PyDyna knowledge records (track T1, step T1.0).

Reads the JSONL files in ``knowledge/pydyna/knowledge-base/`` and turns each
record into ``(id, document, metadata)`` for the three collections of the old
PyDyna_Agent (built there by ``sacreate.py``):

=================  =========================================================
Collection         Files
=================  =========================================================
lsdyna_keywords    ``keyword_schema.jsonl`` (or ``keyword_schema-part-*``),
                   then ``keyword_reference.jsonl`` (curated)
pydyna_api         ``pydyna_docs.jsonl``, then ``pydyna_source-part-*.jsonl``
                   (or ``pydyna_source.jsonl``)
pydpf_post         ``pydpf_post_source.jsonl``
=================  =========================================================

Document text and metadata follow ``sacreate.py`` so search behaves as before.
Fixes against the old agent (docs/tracks/pydyna.md, "Problems found"):

1. A curated keyword is merged over the generated one instead of replacing it:
   all generated fields stay, the curated values win where both have a value.
2. Ids are stable (built from the keyword name or the source path and chunk),
   not ``kw_0``, ``api_0``: they do not change when the input order changes.
3. Code collections keep only ``.py``, ``.rst``, ``.md`` files under ``src/``, ``doc/``,
   ``docs/``, ``examples/`` (user, 21 Nov 2026: option A); tests, ``codegen/`` and
   repository files (``.pre-commit-config.yaml``, ``CODE_OF_CONDUCT.md``, ...) are
   left out. PyDyna docs records without a source file are kept.
4. ``check_params`` also checks field types.

Pure Python: no Chroma, no model, nothing is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

KW_COLLECTION = "lsdyna_keywords"
API_COLLECTION = "pydyna_api"
DPF_POST_COLLECTION = "pydpf_post"

KW_SCHEMA_FILE = "keyword_schema.jsonl"
KW_SCHEMA_SHARDS = "keyword_schema-part-*.jsonl"
KW_REF_FILE = "keyword_reference.jsonl"
API_DOCS_FILE = "pydyna_docs.jsonl"
API_SOURCE_FILE = "pydyna_source.jsonl"
API_SOURCE_SHARDS = "pydyna_source-part-*.jsonl"
DPF_POST_FILE = "pydpf_post_source.jsonl"

GENERATED_SOURCE = "PyDyna codegen schema"
CURATED_SOURCE = "curated reference"

# Code collections (pydyna_api, pydpf_post): only these top folders ...
CODE_FOLDERS = frozenset({"src", "doc", "docs", "examples"})
# ... and file types are kept ...
CODE_SUFFIXES = frozenset({".py", ".rst", ".md"})
# ... and never these repository files (compared in upper case, without suffix).
CODE_META_NAMES = frozenset({
    "CODE_OF_CONDUCT", "CONTRIBUTING", "CONTRIBUTORS", "AUTHORS", "SECURITY",
    "LICENSE", "CHANGELOG", "CHANGES", "PULL_REQUEST_TEMPLATE", "ISSUE_TEMPLATE",
})

# Field types of the schema (extract_lsdyna_keywords.py TYPE_NAMES) -> Python types.
_TYPE_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
}


@dataclass(frozen=True)
class Document:
    """One entry of a collection, ready for the knowledge layer to index."""

    id: str
    text: str
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------- reading

def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield the records of a JSONL file; blank lines are skipped, a bad line is an error."""
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} line {number}: not valid JSON ({exc.msg})") from None
            if not isinstance(record, dict):
                raise ValueError(f"{path.name} line {number}: expected a JSON object")
            yield record


def _shards_or_file(kb_dir: Path, shards: str, single: str) -> list[Path]:
    found = sorted(kb_dir.glob(shards))
    if found:
        return found
    return [kb_dir / single] if (kb_dir / single).is_file() else []


def keyword_schema_files(kb_dir: Path) -> list[Path]:
    return _shards_or_file(Path(kb_dir), KW_SCHEMA_SHARDS, KW_SCHEMA_FILE)


def api_files(kb_dir: Path) -> list[Path]:
    kb_dir = Path(kb_dir)
    docs = [kb_dir / API_DOCS_FILE] if (kb_dir / API_DOCS_FILE).is_file() else []
    return docs + _shards_or_file(kb_dir, API_SOURCE_SHARDS, API_SOURCE_FILE)


# ---------------------------------------------------------------- keywords

def normalize_keyword(name: str) -> str:
    """``mat_024`` -> ``*MAT_024``."""
    text = str(name).strip().upper()
    return text if text.startswith("*") else "*" + text


def merge_keyword(generated: dict, curated: dict) -> dict:
    """Merge a curated keyword record over the generated one.

    Every generated field stays (card, options, transform, ...). For a field in
    both, the curated values win key by key (description, required, unit,
    min, max, default). Curated-only fields are added. The keyword description
    becomes the curated one; the generated text is kept as ``schema_description``.
    """
    merged = dict(generated)
    parameters = {name: dict(spec) for name, spec in (generated.get("parameters") or {}).items()}
    for name, spec in (curated.get("parameters") or {}).items():
        parameters[name] = {**parameters.get(name, {}), **spec}
    merged["parameters"] = parameters
    if curated.get("description"):
        if generated.get("description"):
            merged["schema_description"] = generated["description"]
        merged["description"] = curated["description"]
    if curated.get("category"):
        merged["category"] = curated["category"]
    aliases = []
    for alias in [*(generated.get("aliases") or []), *(curated.get("aliases") or [])]:
        alias = normalize_keyword(alias)
        if alias not in aliases:
            aliases.append(alias)
    merged["aliases"] = aliases
    merged["sources"] = [GENERATED_SOURCE, CURATED_SOURCE]
    return merged


def load_keywords(kb_dir: Path) -> dict[str, dict]:
    """All keyword records by normalized name: generated first, curated merged over them.

    A keyword that only the curated file has is added with ``sources = [curated]``.
    Within one file a later record of the same keyword replaces the earlier one
    (as in ``sacreate.py``).
    """
    kb_dir = Path(kb_dir)
    records: dict[str, dict] = {}
    for path in keyword_schema_files(kb_dir):
        for record in read_jsonl(path):
            name = normalize_keyword(record["keyword"])
            records[name] = {**record, "keyword": name, "sources": [GENERATED_SOURCE]}
    curated_path = kb_dir / KW_REF_FILE
    if curated_path.is_file():
        for record in read_jsonl(curated_path):
            name = normalize_keyword(record["keyword"])
            record = {**record, "keyword": name}
            if name in records and GENERATED_SOURCE in records[name]["sources"]:
                records[name] = merge_keyword(records[name], record)
            else:
                aliases = [normalize_keyword(a) for a in record.get("aliases") or []]
                records[name] = {**record, "aliases": aliases, "sources": [CURATED_SOURCE]}
    return records


def keyword_index(records: dict[str, dict]) -> dict[str, dict]:
    """Names and aliases -> record. An alias never hides a real keyword of the same name."""
    index = dict(records)
    for record in records.values():
        for alias in record.get("aliases") or []:
            index.setdefault(normalize_keyword(alias), record)
    return index


def keyword_document(record: dict) -> Document:
    """Text as in ``sacreate.py`` (keyword, category, description), plus the schema text after a merge."""
    parts = [record.get("keyword", ""), record.get("category", ""), record.get("description", "")]
    if record.get("schema_description"):
        parts.append(record["schema_description"])
    return Document(
        id="kw:" + record["keyword"],
        text=" ".join(part for part in parts if part),
        metadata={
            "keyword": record.get("keyword", ""),
            "category": record.get("category", ""),
            "sources": ", ".join(record.get("sources") or []),
        },
    )


def keyword_documents(kb_dir: Path) -> list[Document]:
    return [keyword_document(record) for record in load_keywords(kb_dir).values()]


# ---------------------------------------------------------------- API and PyDPF-Post

def _chunk_id(prefix: str, record: dict) -> str:
    source_path = record.get("source_path") or ""
    if source_path:
        return f"{prefix}:{source_path}#{record.get('chunk_index', -1)}"
    return f"{prefix}:doc:{record.get('class', '')}.{record.get('method', '')}"


def api_document(record: dict) -> Document:
    """Text and metadata as ``sacreate.py`` builds them for ``pydyna_api``."""
    parts = [
        f"{record.get('class', '')}.{record.get('method', '')}",
        f"Source: {record['source_path']}" if record.get("source_path") else "",
        record.get("description", ""),
        f"Signature: {record['signature']}" if record.get("signature") else "",
        f"Example: {record['example']}" if record.get("example") else "",
    ]
    return Document(
        id=_chunk_id("api", record),
        text="\n".join(part for part in parts if part),
        metadata={
            "class": record.get("class", ""),
            "method": record.get("method", ""),
            "source_path": record.get("source_path", ""),
            "chunk_index": record.get("chunk_index", -1),
        },
    )


def api_documents(kb_dir: Path) -> list[Document]:
    return unique_ids(api_document(record) for path in api_files(kb_dir) for record in read_jsonl(path)
                      if keep_api_path(record.get("source_path", "")))


def keep_code_path(source_path: str) -> bool:
    """True for source, docs and examples (``src/``, ``doc/``, ``docs/``, ``examples/``); False for
    tests, ``codegen/``, files at the top of the repository and repository meta files."""
    path = PurePosixPath(source_path or "")
    if len(path.parts) < 2 or path.parts[0] not in CODE_FOLDERS:
        return False
    if path.suffix.lower() not in CODE_SUFFIXES or any(part.startswith(".") for part in path.parts):
        return False
    return path.stem.upper() not in CODE_META_NAMES


def keep_api_path(source_path: str) -> bool:
    """``pydyna_api``: the curated docs records have no source file and are kept."""
    return not source_path or keep_code_path(source_path)


def dpf_post_document(record: dict) -> Document:
    """Text and metadata as ``sacreate.py`` builds them for ``pydpf_post``."""
    parts = [
        f"{record.get('class', '')}.{record.get('method', '')}",
        f"Source: {record.get('source_path', '')}",
        record.get("description", ""),
    ]
    return Document(
        id=_chunk_id("dpfpost", record),
        text="\n".join(part for part in parts if part),
        metadata={
            "class": record.get("class", ""),
            "method": record.get("method", ""),
            "source_path": record.get("source_path", ""),
            "source_project": record.get("source_project", "PyDPF-Post"),
            "chunk_index": record.get("chunk_index", -1),
        },
    )


def dpf_post_documents(kb_dir: Path) -> list[Document]:
    path = Path(kb_dir) / DPF_POST_FILE
    if not path.is_file():
        return []
    return unique_ids(dpf_post_document(record) for record in read_jsonl(path)
                      if keep_code_path(record.get("source_path", "")))


def unique_ids(documents: Iterable[Document]) -> list[Document]:
    """Return the documents as a list; two with the same id is an error (ids must be stable and unique)."""
    result, seen = [], set()
    for document in documents:
        if document.id in seen:
            raise ValueError(f"duplicate record id {document.id}")
        seen.add(document.id)
        result.append(document)
    return result


def collection_documents(kb_dir: Path) -> dict[str, list[Document]]:
    """All three collections (keyword ids are unique by construction)."""
    return {
        KW_COLLECTION: keyword_documents(kb_dir),
        API_COLLECTION: api_documents(kb_dir),
        DPF_POST_COLLECTION: dpf_post_documents(kb_dir),
    }


# ---------------------------------------------------------------- parameter check

def check_params(record: dict, params: dict) -> dict:
    """Check card parameters against a keyword record.

    Errors: a required field is missing, a value has the wrong type (``int``,
    ``float``, ``string``; a bool is never a number). Warnings: unknown field,
    value below ``min`` or above ``max``. Field types the schema names otherwise
    are not checked.
    """
    specs = record.get("parameters") or {}
    errors, warnings = [], []
    for name, spec in specs.items():
        if spec.get("required") and name not in params:
            errors.append(f"Missing required parameter: {name}")
    for name, value in params.items():
        spec = specs.get(name)
        if spec is None:
            warnings.append(f"Unknown parameter: {name}")
            continue
        check = _TYPE_CHECKS.get(str(spec.get("type", "")).lower())
        if check is not None and not check(value):
            errors.append(f"{name}={value!r} is not of type {spec['type']}")
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "min" in spec and value < spec["min"]:
                warnings.append(f"{name}={value} is below minimum ({spec['min']})")
            if "max" in spec and value > spec["max"]:
                warnings.append(f"{name}={value} is above maximum ({spec['max']})")
    return {"valid": not errors, "keyword": record.get("keyword", ""), "errors": errors, "warnings": warnings}
