"""Search in the PyDyna Chroma database (track T1, stub until M1).

PLAN.md P.2 allows a track to work around a missing core feature with a stub in
its own package. This is that stub for M1 (knowledge layer): it opens the
existing Chroma database of the old PyDyna_Agent read-only by intent (nothing
is added or deleted) and runs one query. When M1 lands, the tools call M1's
search instead and this module is removed.

* Database: ``pydyna.database_path`` (default ``<project>/knowledge/pydyna/vector_db``).
* Embedding model: ``BAAI/bge-small-en-v1.5`` on CPU, the model the database was built
  with (vector length 384, checked 21 Nov 2026). Loaded on the first search.
* Chroma telemetry is switched off: no data leaves the machine.
* chromadb and sentence-transformers are imported only on the first search
  (optional dependency group ``pydyna``); without them the search tools report
  how to install them, and lookups still work.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

EMBED_MODEL = "BAAI/bge-small-en-v1.5"


@dataclass(frozen=True)
class Hit:
    id: str
    document: str
    metadata: dict
    distance: float

    @property
    def score(self) -> float:
        """``1 - distance`` as the old agent showed it (higher is closer)."""
        return round(1.0 - self.distance, 3)


class SearchBackend(Protocol):
    def query(self, collection: str, text: str, n_results: int, where: dict | None = None) -> list[Hit]: ...


class SearchUnavailable(Exception):
    """The database or the search packages are missing; the message says what to do."""


class ChromaSearch:
    """One persistent Chroma client per database folder, collections opened on first use."""

    def __init__(self, db_path: Path, model_name: str = EMBED_MODEL, embedding: Any = None):
        self.db_path = Path(db_path)
        self.model_name = model_name
        self._client: Any = None
        self._embedding: Any = embedding  # None: the sentence-transformers model, loaded on first use
        self._collections: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _open(self, collection: str) -> Any:
        with self._lock:
            if collection in self._collections:
                return self._collections[collection]
            if not (self.db_path / "chroma.sqlite3").is_file():
                raise SearchUnavailable(f"no PyDyna database at {self.db_path} (chroma.sqlite3 missing); "
                                        "set pydyna.database_path in the config")
            try:
                import chromadb
                from chromadb.config import Settings
                from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            except ImportError as exc:
                raise SearchUnavailable(f"search needs the optional packages chromadb and sentence-transformers "
                                        f"(pip install -e '.[pydyna]'); {exc}") from None
            if self._client is None:
                self._client = chromadb.PersistentClient(
                    path=str(self.db_path), settings=Settings(anonymized_telemetry=False, allow_reset=False))
                if self._embedding is None:
                    self._embedding = SentenceTransformerEmbeddingFunction(model_name=self.model_name, device="cpu")
            try:
                handle = self._client.get_collection(collection, embedding_function=self._embedding)
            except Exception as exc:  # chromadb raises its own NotFound types
                raise SearchUnavailable(f"collection {collection} not found in {self.db_path} ({exc})") from None
            self._collections[collection] = handle
            return handle

    def query(self, collection: str, text: str, n_results: int, where: dict | None = None) -> list[Hit]:
        handle = self._open(collection)
        kwargs: dict[str, Any] = {"query_texts": [text], "n_results": n_results,
                                  "include": ["documents", "metadatas", "distances"]}
        if where:
            kwargs["where"] = where
        result = handle.query(**kwargs)
        return [Hit(id=i, document=d or "", metadata=dict(m or {}), distance=float(dist))
                for i, d, m, dist in zip(result["ids"][0], result["documents"][0],
                                         result["metadatas"][0], result["distances"][0])]


_BACKENDS: dict[Path, SearchBackend] = {}
_BACKENDS_LOCK = threading.Lock()


def backend_for(db_path: Path) -> SearchBackend:
    """The shared backend of a database folder (the model is loaded once per Champion run)."""
    key = Path(db_path).resolve()
    with _BACKENDS_LOCK:
        if key not in _BACKENDS:
            _BACKENDS[key] = ChromaSearch(key)
        return _BACKENDS[key]


def set_backend(db_path: Path, backend: SearchBackend | None) -> None:
    """Use another backend for a folder (tests); ``None`` removes it."""
    key = Path(db_path).resolve()
    with _BACKENDS_LOCK:
        if backend is None:
            _BACKENDS.pop(key, None)
        else:
            _BACKENDS[key] = backend
