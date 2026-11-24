"""Chroma database opened in place, read only (M1).

Chroma has no read-only mode, and the PyDyna track noted that ``PersistentClient`` can
write when it starts. Decided with the user (24 Nov 2026): open the database where it is
(no copy) and stop if any file changes:

* before and after the client opens, a fingerprint of every file in the folder (size,
  modification time and, with ``knowledge.verify_hash: true``, SHA-256) is taken; if they
  differ, the database is not used (``DatabaseChanged``);
* after every query the size and modification time are checked again (cheap).

Only ``get_collection``, ``count``, ``query`` and ``get`` are called; nothing is added or
deleted. Chroma telemetry is off. Queries pass vectors (``query_embeddings``), so Chroma
never loads an embedding model of its own.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from champion.knowledge.root import KnowledgeError

SQLITE = "chroma.sqlite3"


class DatabaseChanged(KnowledgeError):
    """A file of the database changed while Champion had it open (or while opening it)."""


@dataclass(frozen=True)
class Hit:
    id: str
    document: str
    metadata: dict = field(default_factory=dict)
    distance: float | None = None  # raw Chroma distance; None for a lookup (no query)

    @property
    def score(self) -> float | None:
        """``1 - distance`` as PyDyna_Agent showed it (higher is closer; can be below 0 for l2)."""
        return None if self.distance is None else round(1.0 - self.distance, 3)

    @property
    def source(self) -> str:
        meta = self.metadata
        return str(meta.get("source_path") or meta.get("source") or meta.get("file") or meta.get("path") or "")

    @property
    def chunk(self) -> str:
        meta = self.metadata
        for key in ("chunk_index", "chunk", "chunk_id"):
            if meta.get(key) not in (None, ""):
                return str(meta[key])
        return ""


def fingerprint(db_dir: Path, with_hash: bool) -> dict[str, tuple]:
    """``relative path -> (size, mtime_ns[, sha256])`` of every file in the database folder."""
    result: dict[str, tuple] = {}
    db_dir = Path(db_dir)
    for folder, dirs, files in os.walk(db_dir, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_symlink():
                raise KnowledgeError(f"{path} is a symbolic link; links are not followed")
            info = path.stat()
            entry: tuple = (info.st_size, info.st_mtime_ns)
            if with_hash:
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(block)
                entry += (digest.hexdigest(),)
            result[str(path.relative_to(db_dir))] = entry
    return result


def _stat_only(prints: Mapping[str, tuple]) -> dict[str, tuple]:
    return {name: value[:2] for name, value in prints.items()}


def _differences(before: Mapping[str, tuple], after: Mapping[str, tuple]) -> list[str]:
    names = sorted(set(before) | set(after))
    return [name for name in names if before.get(name) != after.get(name)]


def _where(filters: Mapping[str, Any] | None) -> dict | None:
    """Equality filters as a Chroma ``where`` (``$and`` for more than one)."""
    items = [(key, value) for key, value in (filters or {}).items() if value is not None]
    if not items:
        return None
    if len(items) == 1:
        return {items[0][0]: items[0][1]}
    return {"$and": [{key: value} for key, value in items]}


class ReadOnlyStore:
    def __init__(self, db_dir: Path, verify_hash: bool = True):
        self.db_dir = Path(db_dir)
        self.verify_hash = verify_hash
        self._client: Any = None
        self._stat: dict[str, tuple] = {}
        self._collections: dict[str, Any] = {}
        self._broken: str | None = None
        self._lock = threading.Lock()

    def _open_client(self) -> Any:
        if self._broken:
            raise DatabaseChanged(self._broken)
        if self._client is not None:
            return self._client
        if self.db_dir.is_symlink() or not (self.db_dir / SQLITE).is_file():
            raise KnowledgeError(f"no database at {self.db_dir} ({SQLITE} missing); build it with champion kb build")
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise KnowledgeError("search needs the optional packages chromadb and sentence-transformers "
                                 f"(pip install -e '.[knowledge]'); {exc}") from None
        before = fingerprint(self.db_dir, self.verify_hash)
        client = chromadb.PersistentClient(path=str(self.db_dir),
                                           settings=Settings(anonymized_telemetry=False, allow_reset=False))
        after = fingerprint(self.db_dir, self.verify_hash)
        changed = _differences(before, after)
        if changed:
            self._broken = (f"database {self.db_dir} changed while it was opened ({', '.join(changed[:5])}); "
                            "Champion stopped using it")
            raise DatabaseChanged(self._broken)
        self._client, self._stat = client, _stat_only(after)
        return client

    def check_unchanged(self) -> None:
        if self._client is None:
            return
        changed = _differences(self._stat, _stat_only(fingerprint(self.db_dir, False)))
        if changed:
            self._broken = (f"database {self.db_dir} changed while Champion had it open "
                            f"({', '.join(changed[:5])}); Champion stopped using it")
            self._client, self._collections = None, {}
            raise DatabaseChanged(self._broken)

    def collection_names(self) -> list[str]:
        with self._lock:
            client = self._open_client()
            return sorted(getattr(c, "name", str(c)) for c in client.list_collections())

    def _collection(self, name: str) -> Any:
        with self._lock:
            if name in self._collections:
                return self._collections[name]
            client = self._open_client()
            try:
                handle = client.get_collection(name)
            except Exception:  # chromadb raises its own NotFound types
                known = sorted(getattr(c, "name", str(c)) for c in client.list_collections())
                raise KnowledgeError(f"collection {name} not found in {self.db_dir} "
                                     f"(there: {', '.join(known) or 'none'})") from None
            self._collections[name] = handle
            return handle

    def count(self, name: str) -> int:
        return int(self._collection(name).count())

    def space(self, name: str) -> str:
        meta = getattr(self._collection(name), "metadata", None) or {}
        return str(meta.get("hnsw:space", "l2"))  # Chroma's default when none was set

    def query(self, name: str, vector: Sequence[float], n_results: int,
              filters: Mapping[str, Any] | None = None) -> list[Hit]:
        handle = self._collection(name)
        kwargs: dict[str, Any] = {"query_embeddings": [list(vector)], "n_results": n_results,
                                  "include": ["documents", "metadatas", "distances"]}
        where = _where(filters)
        if where:
            kwargs["where"] = where
        try:
            result = handle.query(**kwargs)
        except Exception as exc:  # wrong vector length, bad filter ...
            raise KnowledgeError(f"search in {name} failed ({type(exc).__name__}: {str(exc)[:200]})") from None
        finally:
            self.check_unchanged()
        return [Hit(str(i), d or "", dict(m or {}), float(dist))
                for i, d, m, dist in zip(result["ids"][0], result["documents"][0],
                                         result["metadatas"][0], result["distances"][0])]

    def get(self, name: str, *, ids: Sequence[str] | None = None, filters: Mapping[str, Any] | None = None,
            limit: int = 5) -> list[Hit]:
        handle = self._collection(name)
        kwargs: dict[str, Any] = {"include": ["documents", "metadatas"], "limit": limit}
        if ids:
            kwargs["ids"] = list(ids)
        where = _where(filters)
        if where:
            kwargs["where"] = where
        try:
            result = handle.get(**kwargs)
        except Exception as exc:
            raise KnowledgeError(f"lookup in {name} failed ({type(exc).__name__}: {str(exc)[:200]})") from None
        finally:
            self.check_unchanged()
        return [Hit(str(i), d or "", dict(m or {}))
                for i, d, m in zip(result["ids"], result["documents"], result["metadatas"])]


_STORES: dict[tuple[Path, bool], ReadOnlyStore] = {}
_STORES_LOCK = threading.Lock()


def store_for(db_dir: Path, verify_hash: bool = True) -> ReadOnlyStore:
    """The shared store of a database folder (opened once per Champion run)."""
    key = (Path(db_dir), bool(verify_hash))
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = ReadOnlyStore(*key)
        return _STORES[key]


def forget_store(db_dir: Path | None = None) -> None:
    """Drop the open store of a folder (all with ``None``), for example after a build."""
    with _STORES_LOCK:
        for key in [k for k in _STORES if db_dir is None or k[0] == Path(db_dir)]:
            _STORES.pop(key)
