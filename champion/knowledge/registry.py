"""Which agents have a knowledge base and what is in it (M1).

A track declares its collections once, in its own package, for example::

    register_knowledge(KnowledgeSpec("pydyna", (
        KnowledgeCollection("lsdyna_keywords", model="BAAI/bge-small-en-v1.5",
                            documents=keyword_documents, files=keyword_files),
        KnowledgeCollection("pydpf_core", database="index_dpf_core", model=..., keep=keep_code_path),
    )))

The search tools, ``champion kb status`` / ``build`` and ``champion doctor`` read this;
the track never opens the store itself. ``documents`` and ``files`` are only needed for
``champion kb build``; ``keep`` drops hits at search time (for example test files).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from champion.knowledge.manifest import SPACES
from champion.knowledge.root import AGENT_NAME, DATABASE_NAME, DEFAULT_DATABASE, KnowledgeError

COLLECTION_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}[a-zA-Z0-9]$")  # also a file name


@dataclass(frozen=True)
class Document:
    """One entry to index: stable id, text to embed and metadata (str, int, float or bool values)."""
    id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeCollection:
    name: str
    model: str  # embedding model the collection is (or will be) built with
    database: str = DEFAULT_DATABASE  # store folder in the root: index or index_<name>
    space: str = "l2"  # distance of the collection (l2 = squared Euclidean, as Chroma)
    description: str = ""
    documents: Callable[[Path], Iterable[Document]] | None = field(default=None, compare=False)
    files: Callable[[Path], list[Path]] | None = field(default=None, compare=False)
    keep: Callable[[Any], bool] | None = field(default=None, compare=False)  # Hit -> keep it?


@dataclass(frozen=True)
class KnowledgeSpec:
    agent: str  # default root knowledge/<agent>
    collections: tuple[KnowledgeCollection, ...]
    config_section: str | None = None  # section with knowledge_root; None = the agent's name

    def collection(self, name: str) -> KnowledgeCollection:
        for item in self.collections:
            if item.name == name:
                return item
        raise KnowledgeError(f"{self.agent} has no collection {name} "
                             f"(declared: {', '.join(c.name for c in self.collections)})")


def spec_problems(spec: KnowledgeSpec) -> list[str]:
    problems = []
    if not AGENT_NAME.match(spec.agent or ""):
        problems.append(f"agent name {spec.agent!r} is not valid")
    if not spec.collections:
        problems.append("no collections declared")
    seen = set()
    for item in spec.collections:
        if not COLLECTION_NAME.match(item.name or ""):
            problems.append(f"collection name {item.name!r}: 3-64 letters, digits, ., _ or -")
        if item.name in seen:
            problems.append(f"collection {item.name} is declared twice")
        seen.add(item.name)
        if not DATABASE_NAME.match(item.database or ""):
            problems.append(f"{item.name}: database must be index or index_<name>")
        if not isinstance(item.model, str) or not item.model.strip():
            problems.append(f"{item.name}: model is empty")
        if item.space not in SPACES:
            problems.append(f"{item.name}: space must be one of {', '.join(SPACES)}")
    return problems


_SPECS: dict[str, KnowledgeSpec] = {}


def register_knowledge(spec: KnowledgeSpec) -> KnowledgeSpec:
    problems = spec_problems(spec)
    if problems:
        raise ValueError(f"knowledge of {spec.agent}: {'; '.join(problems)}")
    existing = _SPECS.get(spec.agent)
    if existing is not None and existing != spec:
        raise ValueError(f"knowledge of {spec.agent} is already registered with other collections")
    _SPECS[spec.agent] = spec
    return spec


def unregister_knowledge(agent: str) -> None:
    _SPECS.pop(agent, None)


def knowledge_specs() -> list[KnowledgeSpec]:
    return [_SPECS[name] for name in sorted(_SPECS)]


def knowledge_spec(agent: str) -> KnowledgeSpec:
    try:
        return _SPECS[agent]
    except KeyError:
        known = ", ".join(sorted(_SPECS)) or "none"
        raise KnowledgeError(f"no knowledge base is declared for {agent} (declared: {known})") from None
