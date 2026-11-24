"""Search and lookup in a declared collection (M1).

``search`` embeds the question with the collection's model (offline), asks Chroma for the
nearest entries and returns them with the raw distance, a score (``1 - distance``), the
source file and the chunk. With a ``keep`` function (declared by the track) more entries
are fetched (``knowledge.fetch_factor`` x, at most 100) and the dropped ones are left out.
``lookup`` returns entries by id or by exact metadata values, without embedding.

Before the first search of a collection its manifest entry (if there is one) must match
the declared model; the vector length must match the manifest's dimension.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from champion.knowledge.embedding import embedder_for
from champion.knowledge.manifest import CollectionInfo, read_manifest
from champion.knowledge.registry import KnowledgeCollection, KnowledgeSpec, knowledge_spec
from champion.knowledge.root import KnowledgeError, KnowledgeRoot, resolve_root
from champion.knowledge.store import Hit, store_for

MAX_FETCH = 100


@dataclass(frozen=True)
class Target:
    spec: KnowledgeSpec
    collection: KnowledgeCollection
    root: KnowledgeRoot
    info: CollectionInfo | None  # manifest entry; None when the database has no manifest

    @property
    def db_dir(self):
        return self.root.db_dir(self.collection.database)


def target(config: Any, agent: str, collection: str) -> Target:
    spec = knowledge_spec(agent)
    item = spec.collection(collection)
    root = resolve_root(config, agent, spec.config_section)
    manifest = read_manifest(root.db_dir(item.database))
    info = (manifest or {}).get(item.name)
    if manifest is not None and info is None:
        raise KnowledgeError(f"{agent}/{collection}: not in {item.database}/MANIFEST.json; "
                             f"build it with champion kb build --agent {agent} --collection {collection}")
    if info is not None and info.model != item.model:
        raise KnowledgeError(f"{agent}/{collection} was built with {info.model}, but the agent uses {item.model}; "
                             "the vectors would not match; build the collection again")
    return Target(spec, item, root, info)


def _limits(config: Any) -> tuple[int, int]:
    return int(config.get("knowledge.max_results", 20)), int(config.get("knowledge.fetch_factor", 5))


def search(config: Any, agent: str, collection: str, text: str, n_results: int = 8,
           filters: Mapping[str, Any] | None = None) -> list[Hit]:
    if not isinstance(text, str) or not text.strip():
        raise KnowledgeError("the search text is empty")
    found = target(config, agent, collection)
    max_results, factor = _limits(config)
    n = max(1, min(int(n_results or 8), max_results))
    vector = embedder_for(found.collection.model, bool(config.get("knowledge.offline", True))).embed([text])[0]
    if found.info is not None and len(vector) != found.info.dimension:
        raise KnowledgeError(f"{agent}/{collection}: the model gives vectors of length {len(vector)}, "
                             f"the database has {found.info.dimension}")
    store = store_for(found.db_dir, bool(config.get("knowledge.verify_hash", True)))
    keep = found.collection.keep
    fetch = n if keep is None else min(n * factor, MAX_FETCH)
    hits = store.query(found.collection.name, vector, fetch, filters)
    if keep is not None:
        hits = [hit for hit in hits if keep(hit)]
    return hits[:n]


def lookup(config: Any, agent: str, collection: str, *, ids: Sequence[str] | None = None,
           filters: Mapping[str, Any] | None = None, limit: int = 5) -> list[Hit]:
    if not ids and not any(v is not None for v in (filters or {}).values()):
        raise KnowledgeError("a lookup needs ids or at least one exact value")
    found = target(config, agent, collection)
    max_results, _ = _limits(config)
    store = store_for(found.db_dir, bool(config.get("knowledge.verify_hash", True)))
    return store.get(found.collection.name, ids=ids, filters=filters, limit=max(1, min(int(limit or 5), max_results)))


def format_hits(hits: Sequence[Hit], max_chars: int, title: str) -> str:
    """Hits as text for the model: number, score, distance, source and chunk, then the text."""
    if not hits:
        return f"{title}: no results"
    lines = [f"{title}: {len(hits)} result(s)"]
    for number, hit in enumerate(hits, 1):
        parts = [f"[{number}] id {hit.id}"]
        if hit.distance is not None:
            parts.append(f"score {hit.score:.3f} (distance {hit.distance:.4f})")
        if hit.source:
            parts.append(f"source {hit.source}" + (f" chunk {hit.chunk}" if hit.chunk else ""))
        lines.append(" | ".join(parts))
        text = hit.document.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f" [... {len(hit.document) - max_chars} more characters]"
        lines.append(text)
    return "\n".join(lines)
