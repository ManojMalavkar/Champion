"""``MANIFEST.json`` of a database folder (M1).

One manifest per database folder (``vector_db``, ``vector_db_<name>``), in the format the
PyDyna track already writes (T1.3b ``build_index.py``)::

    {"format": 1,
     "collections": {"pydpf_core": {"count": 13014, "model": "BAAI/bge-small-en-v1.5",
                                    "dimension": 384, "space": "l2", "built_at": "...",
                                    "files": [{"name": "x.jsonl", "bytes": 1, "sha256": "..."}]}}}

``format`` is optional (missing = 1). A database without a manifest (for example the old
PyDyna_Agent database) can still be searched; its counts and model are then not checked
and ``champion kb status`` says so.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from champion.knowledge.root import KnowledgeError

MANIFEST = "MANIFEST.json"
FORMAT = 1
SPACES = ("l2", "cosine", "ip")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FileEntry:
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class CollectionInfo:
    name: str
    count: int
    model: str
    dimension: int
    space: str = "l2"
    built_at: str | None = None
    files: tuple[FileEntry, ...] = field(default=())

    def to_json(self) -> dict[str, Any]:
        return {"count": self.count, "model": self.model, "dimension": self.dimension, "space": self.space,
                "built_at": self.built_at,
                "files": [{"name": f.name, "bytes": f.bytes, "sha256": f.sha256} for f in self.files]}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _collection(name: str, raw: Any) -> CollectionInfo:
    where = f"{MANIFEST}: collection {name}"
    if not isinstance(raw, dict):
        raise KnowledgeError(f"{where} must be a mapping")
    count, model, dimension, space = raw.get("count"), raw.get("model"), raw.get("dimension"), raw.get("space", "l2")
    if not _is_int(count) or count < 0:
        raise KnowledgeError(f"{where}: count must be a whole number (got {count!r})")
    if not isinstance(model, str) or not model:
        raise KnowledgeError(f"{where}: model must be the embedding model name")
    if not _is_int(dimension) or dimension <= 0:
        raise KnowledgeError(f"{where}: dimension must be a whole number above 0 (got {dimension!r})")
    if space not in SPACES:
        raise KnowledgeError(f"{where}: space must be one of {', '.join(SPACES)} (got {space!r})")
    files = []
    for entry in raw.get("files") or []:
        if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]
                or "/" in entry["name"] or entry["name"] in (".", "..") or not _is_int(entry.get("bytes"))
                or not SHA256.match(str(entry.get("sha256", "")))):
            raise KnowledgeError(f"{where}: file entries need name (no folder), bytes and sha256 (got {entry!r})")
        files.append(FileEntry(entry["name"], entry["bytes"], entry["sha256"]))
    built_at = raw.get("built_at")
    return CollectionInfo(name, count, model, dimension, space, built_at if isinstance(built_at, str) else None,
                          tuple(files))


def read_manifest(db_dir: Path) -> dict[str, CollectionInfo] | None:
    """Collections of the manifest in ``db_dir``; ``None`` when there is no manifest."""
    path = Path(db_dir) / MANIFEST
    if path.is_symlink():
        raise KnowledgeError(f"{path} is a symbolic link; links are not followed")
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeError(f"{path} cannot be read ({type(exc).__name__}: {exc})") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("collections"), dict):
        raise KnowledgeError(f"{path} must contain {{\"collections\": {{...}}}}")
    if raw.get("format", FORMAT) != FORMAT:
        raise KnowledgeError(f"{path}: format {raw.get('format')!r} is not known (this Champion reads {FORMAT})")
    return {name: _collection(name, entry) for name, entry in raw["collections"].items()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_entry(path: Path) -> FileEntry:
    path = Path(path)
    return FileEntry(path.name, path.stat().st_size, sha256_file(path))


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def write_entry(db_dir: Path, info: CollectionInfo) -> None:
    """Add or replace one collection in ``db_dir/MANIFEST.json`` (atomic replace)."""
    db_dir = Path(db_dir)
    current = read_manifest(db_dir) or {}
    current[info.name] = info
    data = {"format": FORMAT, "collections": {name: current[name].to_json() for name in sorted(current)}}
    handle, temp = tempfile.mkstemp(prefix=".MANIFEST.", dir=db_dir)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=1) + "\n")
        os.replace(temp, db_dir / MANIFEST)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def file_problems(info: CollectionInfo, kb_dir: Path) -> list[str]:
    """Source files of a collection that are missing or changed since the build (reads them all)."""
    problems = []
    for entry in info.files:
        path = Path(kb_dir) / entry.name
        if not path.is_file():
            problems.append(f"{entry.name} is missing in {kb_dir}")
        elif path.stat().st_size != entry.bytes or sha256_file(path) != entry.sha256:
            problems.append(f"{entry.name} changed since the build (SHA-256 differs); build {info.name} again")
    return problems
