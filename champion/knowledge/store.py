"""Champion's own knowledge store, read only (M1, CR-4).

A store folder (``index`` or ``index_<name>`` in the agent's knowledge root) holds, per
collection, two files written by ``champion kb build`` and listed with size and SHA-256 in
the folder's ``MANIFEST.json``:

* ``<collection>.npy``: float32 vectors, shape (count, dimension); row i belongs to record i;
* ``<collection>.sqlite3``: table ``records`` (``row``, ``id``, ``document``, ``metadata`` as
  JSON) and table ``info`` (format, model, dimension, space, count).

Search never writes: SQLite is opened with ``mode=ro&immutable=1`` and the vectors with
``numpy.load(mmap_mode="r", allow_pickle=False)``. Chroma is not used (CR-4: chromadb
1.5.9 writes into its database on every open). The search is exact; the distances are
those Chroma uses, so scores stay comparable with the old databases:

* ``l2``: squared Euclidean distance;
* ``cosine``: 1 - cosine similarity;
* ``ip``: 1 - inner product.

When a collection is loaded its files must match the manifest (size; SHA-256 with
``knowledge.verify_hash: true``) and each other (count, dimension, model, space, rows).
The size and modification time of every file in the folder are taken when the store
opens and checked again after every query and lookup; a change stops the store
(``DatabaseChanged``) until Champion is started again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from champion.knowledge.manifest import CollectionInfo, read_manifest, sha256_file
from champion.knowledge.root import KnowledgeError

STORE_FORMAT = 1  # version of the two store files (``info.format``)
FILTER_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SQL_CHUNK = 500  # ids or rows per SQL statement


def vectors_path(db_dir: Path, name: str) -> Path:
    return Path(db_dir) / f"{name}.npy"


def records_path(db_dir: Path, name: str) -> Path:
    return Path(db_dir) / f"{name}.sqlite3"


class DatabaseChanged(KnowledgeError):
    """A file of the store folder changed while Champion had it open."""


@dataclass(frozen=True)
class Hit:
    id: str
    document: str
    metadata: dict = field(default_factory=dict)
    distance: float | None = None  # distance in the collection's space; None for a lookup (no query)

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
    """``relative path -> (size, mtime_ns[, sha256])`` of every file in the folder."""
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


def numpy_module() -> Any:
    try:
        import numpy
    except ImportError as exc:
        raise KnowledgeError(f"the knowledge store needs numpy (pip install -e '.[knowledge]'); {exc}") from None
    return numpy


def connect_read_only(path: Path) -> sqlite3.Connection:
    """SQLite opened so that it cannot write: ``mode=ro`` and ``immutable=1`` (no locks, no journal)."""
    uri = f"file:{urllib.parse.quote(str(path))}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def _same(value: Any, wanted: Any) -> bool:
    return value == wanted and isinstance(value, bool) == isinstance(wanted, bool)


@dataclass
class _Collection:
    info: CollectionInfo
    vectors: Any  # numpy array, memory-mapped read only
    norms: Any  # squared norms (l2), norms (cosine) or None (ip)
    con: sqlite3.Connection


class ReadOnlyStore:
    def __init__(self, db_dir: Path, verify_hash: bool = True):
        self.db_dir = Path(db_dir)
        self.verify_hash = verify_hash
        self._manifest: dict[str, CollectionInfo] | None = None
        self._stat: dict[str, tuple] = {}
        self._collections: dict[str, _Collection] = {}
        self._broken: str | None = None
        self._lock = threading.RLock()

    # ----------------------------------------------------------- opening

    def _open(self) -> dict[str, CollectionInfo]:
        if self._broken:
            raise DatabaseChanged(self._broken)
        if self._manifest is not None:
            return self._manifest
        if self.db_dir.is_symlink():
            raise KnowledgeError(f"{self.db_dir} is a symbolic link; links are not followed")
        manifest = read_manifest(self.db_dir) if self.db_dir.is_dir() else None
        if manifest is None:
            raise KnowledgeError(f"no knowledge store at {self.db_dir} (no MANIFEST.json); "
                                 "build it with champion kb build")
        self._stat = _stat_only(fingerprint(self.db_dir, False))
        self._manifest = manifest
        return manifest

    def _load(self, info: CollectionInfo) -> _Collection:
        np = numpy_module()
        again = f"build {info.name} again with champion kb build --replace"
        if not info.store_files:
            raise KnowledgeError(f"{info.name} in {self.db_dir}/MANIFEST.json lists no store files "
                                 f"(an older build); {again}")
        listed = {entry.name: entry for entry in info.store_files}
        vec_path, rec_path = vectors_path(self.db_dir, info.name), records_path(self.db_dir, info.name)
        for path in (vec_path, rec_path):
            if path.is_symlink():
                raise KnowledgeError(f"{path} is a symbolic link; links are not followed")
            if not path.is_file():
                raise KnowledgeError(f"{path.name} is missing in {self.db_dir}; {again}")
            entry = listed.get(path.name)
            if entry is None:
                raise KnowledgeError(f"{path.name} is not listed in {self.db_dir}/MANIFEST.json; {again}")
            if path.stat().st_size != entry.bytes or (self.verify_hash and sha256_file(path) != entry.sha256):
                raise KnowledgeError(f"{path.name} in {self.db_dir} is not the file MANIFEST.json lists "
                                     f"(size or SHA-256 differs); {again}")
        try:
            vectors = np.load(vec_path, mmap_mode="r", allow_pickle=False)
        except (ValueError, OSError) as exc:
            raise KnowledgeError(f"{vec_path.name} cannot be read as a vector file ({exc}); {again}") from None
        if vectors.dtype != np.float32 or vectors.ndim != 2 or vectors.shape != (info.count, info.dimension):
            raise KnowledgeError(f"{vec_path.name}: expected float32 vectors of shape ({info.count}, "
                                 f"{info.dimension}), found {vectors.dtype} {vectors.shape}; {again}")
        if not bool(np.isfinite(vectors).all()):
            raise KnowledgeError(f"{vec_path.name} contains values that are not finite; {again}")
        con = connect_read_only(rec_path)
        try:
            meta = {str(k): str(v) for k, v in con.execute("select key, value from info")}
            n, low, high = con.execute("select count(*), min(row), max(row) from records").fetchone()
        except sqlite3.DatabaseError as exc:
            con.close()
            raise KnowledgeError(f"{rec_path.name} cannot be read ({exc}); {again}") from None
        problems = []
        if meta.get("format") != str(STORE_FORMAT):
            problems.append(f"format {meta.get('format')!r} (this Champion reads {STORE_FORMAT})")
        for key, value in (("model", info.model), ("dimension", str(info.dimension)), ("space", info.space),
                           ("count", str(info.count))):
            if meta.get(key) != value:
                problems.append(f"{key} {meta.get(key)!r}, MANIFEST.json {value!r}")
        if n != info.count or (n and (low, high) != (0, n - 1)):
            problems.append(f"{n} records (rows {low}..{high}), expected {info.count} (rows 0..{info.count - 1})")
        if problems:
            con.close()
            raise KnowledgeError(f"{rec_path.name} does not match: {'; '.join(problems)}; {again}")
        norms = None
        if info.space in ("l2", "cosine"):
            squared = np.einsum("ij,ij->i", vectors, vectors, dtype=np.float64)
            norms = squared if info.space == "l2" else np.sqrt(squared)
        return _Collection(info, vectors, norms, con)

    def _collection(self, name: str) -> _Collection:
        with self._lock:
            if name in self._collections:
                if self._broken:
                    raise DatabaseChanged(self._broken)
                return self._collections[name]
            manifest = self._open()
            info = manifest.get(name)
            if info is None:
                raise KnowledgeError(f"collection {name} not found in {self.db_dir} "
                                     f"(there: {', '.join(sorted(manifest)) or 'none'})")
            loaded = self._load(info)
            self._collections[name] = loaded
            self.check_unchanged()
            return loaded

    def check_unchanged(self) -> None:
        with self._lock:
            if self._manifest is None:
                return
            changed = _differences(self._stat, _stat_only(fingerprint(self.db_dir, False)))
            if changed:
                self._broken = (f"database {self.db_dir} changed while Champion had it open "
                                f"({', '.join(changed[:5])}); Champion stopped using it")
                self.close()
                raise DatabaseChanged(self._broken)

    def close(self) -> None:
        with self._lock:
            for item in self._collections.values():
                item.con.close()
            self._collections, self._manifest = {}, None

    # ----------------------------------------------------------- reading

    def collection_names(self) -> list[str]:
        with self._lock:
            return sorted(self._open())

    def count(self, name: str) -> int:
        return self._collection(name).info.count

    def space(self, name: str) -> str:
        return self._collection(name).info.space

    def _filter_rows(self, item: _Collection, filters: Mapping[str, Any] | None) -> list[int] | None:
        """Rows whose metadata has all the given values (equality); ``None`` = no filter."""
        wanted = [(key, value) for key, value in (filters or {}).items() if value is not None]
        if not wanted:
            return None
        for key, value in wanted:
            if not isinstance(key, str) or not FILTER_KEY.match(key):
                raise KnowledgeError(f"filter {key!r}: names are letters, digits and _")
            if not isinstance(value, (str, int, float, bool)):
                raise KnowledgeError(f"filter {key}: the value must be text, a number or true/false")
        sql = ("select row from records where " + " and ".join("json_extract(metadata, ?) = ?" for _ in wanted)
               + " order by row")
        params: list[Any] = []
        for key, value in wanted:
            params += [f"$.{key}", value]
        with self._lock:
            try:
                return [row for (row,) in item.con.execute(sql, params)]
            except sqlite3.OperationalError as exc:
                if "json_extract" not in str(exc):
                    raise KnowledgeError(f"filter in {item.info.name} failed ({exc})") from None
                # SQLite without JSON functions: same filter in Python
                rows = item.con.execute("select row, metadata from records order by row")
                return [row for row, meta in rows
                        if all(_same(json.loads(meta).get(key), value) for key, value in wanted)]

    def _records(self, item: _Collection, rows: Sequence[int]) -> dict[int, tuple[str, str, dict]]:
        found: dict[int, tuple[str, str, dict]] = {}
        with self._lock:
            for start in range(0, len(rows), SQL_CHUNK):
                part = list(rows[start:start + SQL_CHUNK])
                marks = ",".join("?" * len(part))
                for row, key, document, meta in item.con.execute(
                        f"select row, id, document, metadata from records where row in ({marks})", part):
                    found[row] = (key, document, json.loads(meta))
        return found

    def query(self, name: str, vector: Sequence[float], n_results: int,
              filters: Mapping[str, Any] | None = None) -> list[Hit]:
        np = numpy_module()
        item = self._collection(name)
        try:
            query = np.asarray(vector, dtype=np.float32)
            if query.shape != (item.info.dimension,):
                raise KnowledgeError(f"search in {name}: the vector has length {query.size}, "
                                     f"the collection {item.info.dimension}")
            if not bool(np.isfinite(query).all()):
                raise KnowledgeError(f"search in {name}: the vector has values that are not finite")
            rows = self._filter_rows(item, filters)
            if rows is not None and not rows:
                return []
            index = None if rows is None else np.asarray(rows, dtype=np.int64)
            vectors = item.vectors if index is None else item.vectors[index]
            dots = (vectors @ query).astype(np.float64)
            space = item.info.space
            if space == "l2":
                norms = item.norms if index is None else item.norms[index]
                distances = np.maximum(norms - 2.0 * dots + float(query.astype(np.float64) @ query), 0.0)
            elif space == "cosine":
                norms = (item.norms if index is None else item.norms[index]) * float(np.linalg.norm(query))
                similarity = np.divide(dots, norms, out=np.zeros_like(dots), where=norms > 0)
                distances = 1.0 - similarity
            else:  # ip
                distances = 1.0 - dots
            k = max(0, min(int(n_results), len(distances)))
            order = np.argsort(distances, kind="stable")[:k]  # ties: lower row first
            chosen = [(int(order_i) if index is None else int(index[order_i]), float(distances[order_i]))
                      for order_i in order]
            records = self._records(item, [row for row, _ in chosen])
            return [Hit(records[row][0], records[row][1], records[row][2], distance) for row, distance in chosen]
        finally:
            self.check_unchanged()

    def get(self, name: str, *, ids: Sequence[str] | None = None, filters: Mapping[str, Any] | None = None,
            limit: int = 5) -> list[Hit]:
        item = self._collection(name)
        try:
            rows = self._filter_rows(item, filters)
            if ids:
                by_id: list[int] = []
                keys = [str(key) for key in ids]
                with self._lock:
                    for start in range(0, len(keys), SQL_CHUNK):
                        part = keys[start:start + SQL_CHUNK]
                        marks = ",".join("?" * len(part))
                        by_id += [row for (row,) in item.con.execute(
                            f"select row from records where id in ({marks})", part)]
                allowed = None if rows is None else set(rows)
                rows = sorted(row for row in by_id if allowed is None or row in allowed)
            if rows is None:
                raise KnowledgeError("a lookup needs ids or at least one exact value")
            rows = rows[:max(0, int(limit))]
            records = self._records(item, rows)
            return [Hit(records[row][0], records[row][1], records[row][2]) for row in rows]
        finally:
            self.check_unchanged()


_STORES: dict[tuple[Path, bool], ReadOnlyStore] = {}
_STORES_LOCK = threading.Lock()


def store_for(db_dir: Path, verify_hash: bool = True) -> ReadOnlyStore:
    """The shared store of a folder (opened once per Champion run)."""
    key = (Path(db_dir), bool(verify_hash))
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = ReadOnlyStore(*key)
        return _STORES[key]


def forget_store(db_dir: Path | None = None) -> None:
    """Close and drop the open store of a folder (all with ``None``), for example before a build."""
    with _STORES_LOCK:
        for key in [k for k in _STORES if db_dir is None or k[0] == Path(db_dir)]:
            _STORES.pop(key).close()
