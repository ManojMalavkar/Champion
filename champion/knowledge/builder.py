"""Build a declared collection from the agent's records (M1, CR-4; ``champion kb build``).

The track supplies the records (``KnowledgeCollection.documents``: knowledge-base folder
-> ``Document``s) and the files they come from (``files``). This module embeds them with
the declared model and writes the collection's two store files (see ``store``) and its
``MANIFEST.json`` entry. Chroma is not used.

Safety rules:
* only the user runs it, on the command line; no model tool can build;
* the store folder (``index`` / ``index_<name>``) is inside the agent's checked knowledge
  root and is not a link; the old Chroma folders (``vector_db*``) cannot be named;
* an existing collection is replaced only with ``replace=True`` (``--replace``);
* ids must be unique text; metadata values text, numbers or true/false (``None`` values are
  left out); vectors finite and all of one length;
* both files are written under temporary names in the store folder, read back and checked,
  and only then renamed into place; the manifest entry is written last. A build that fails
  before the renames leaves the previous files as they were.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Sequence

from champion.knowledge.embedding import Embedder, embedder_for
from champion.knowledge.manifest import CollectionInfo, file_entry, now, read_manifest, write_entry
from champion.knowledge.registry import Document, knowledge_spec
from champion.knowledge.root import KnowledgeError, resolve_root
from champion.knowledge.store import (STORE_FORMAT, connect_read_only, forget_store, numpy_module, records_path,
                                      vectors_path)

BATCH = 256


def _metadata_json(document: Document) -> str:
    meta = {}
    for key, value in dict(document.metadata or {}).items():
        if value is None:
            continue
        if not isinstance(key, str) or not key:
            raise KnowledgeError(f"record {document.id}: metadata names must be text")
        if not isinstance(value, (str, int, float, bool)) or (isinstance(value, float) and not math.isfinite(value)):
            raise KnowledgeError(f"record {document.id}: metadata {key} must be text, a number or true/false "
                                 f"(got {type(value).__name__})")
        meta[key] = value
    return json.dumps(meta, ensure_ascii=False, sort_keys=True)


def _check_records(agent: str, collection: str, documents: Sequence[Document]) -> list[str]:
    if not documents:
        raise KnowledgeError(f"no records for {agent}/{collection}")
    ids = [d.id for d in documents]
    if not all(isinstance(i, str) and i for i in ids):
        raise KnowledgeError(f"{agent}/{collection}: every record needs an id (text)")
    if len(set(ids)) != len(ids):
        raise KnowledgeError(f"{agent}/{collection}: {len(ids) - len(set(ids))} record ids are used twice")
    if not all(isinstance(d.text, str) for d in documents):
        raise KnowledgeError(f"{agent}/{collection}: every record needs a text")
    return [_metadata_json(d) for d in documents]


def _embed(np: Any, embedder: Embedder, agent: str, collection: str, documents: Sequence[Document],
           progress: Callable[[int, int], None] | None) -> Any:
    blocks, dimension = [], None
    for start in range(0, len(documents), BATCH):
        part = documents[start:start + BATCH]
        try:
            block = np.asarray(embedder.embed([d.text for d in part]), dtype=np.float32)
        except ValueError:  # vectors of different lengths
            block = None
        if (block is None or block.ndim != 2 or block.shape[0] != len(part) or block.shape[1] == 0
                or (dimension is not None and block.shape[1] != dimension)):
            raise KnowledgeError(f"{agent}/{collection}: the model returned vectors of different lengths")
        if not bool(np.isfinite(block).all()):
            raise KnowledgeError(f"{agent}/{collection}: the model returned values that are not finite")
        dimension = block.shape[1]
        blocks.append(block)
        if progress is not None:
            progress(min(start + BATCH, len(documents)), len(documents))
    return np.ascontiguousarray(np.concatenate(blocks))


def _write_records(path: Path, info: CollectionInfo, documents: Sequence[Document], metas: Sequence[str]) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("create table info (key text primary key, value text not null)")
        con.execute("create table records (row integer primary key, id text not null unique, "
                    "document text not null, metadata text not null)")
        con.executemany("insert into info values (?, ?)", [
            ("format", str(STORE_FORMAT)), ("model", info.model), ("dimension", str(info.dimension)),
            ("space", info.space), ("count", str(info.count))])
        con.executemany("insert into records values (?, ?, ?, ?)",
                        ((row, d.id, d.text, meta) for row, (d, meta) in enumerate(zip(documents, metas))))
        con.commit()
    finally:
        con.close()


def _read_back(np: Any, vec_tmp: Path, rec_tmp: Path, info: CollectionInfo) -> None:
    check = np.load(vec_tmp, mmap_mode="r", allow_pickle=False)
    shape, dtype = check.shape, check.dtype
    del check
    con = connect_read_only(rec_tmp)
    try:
        rows = con.execute("select count(*) from records").fetchone()[0]
    finally:
        con.close()
    if dtype != np.float32 or shape != (info.count, info.dimension) or rows != info.count:
        raise KnowledgeError(f"{info.name}: the written files do not match ({dtype} {shape}, {rows} records); "
                             "nothing was replaced")


def build_collection(config: Any, agent: str, collection: str, *, replace: bool = False,
                     embedder: Embedder | None = None,
                     progress: Callable[[int, int], None] | None = None) -> CollectionInfo:
    spec = knowledge_spec(agent)
    item = spec.collection(collection)
    if item.documents is None:
        raise KnowledgeError(f"{agent}/{collection} does not say how to build it (no documents function)")
    root = resolve_root(config, agent, spec.config_section)
    db = root.db_dir(item.database)
    if db.is_symlink() or (db.exists() and not db.is_dir()):
        raise KnowledgeError(f"{db} is a symbolic link or not a folder; links are not followed")
    vec_final, rec_final = vectors_path(db, item.name), records_path(db, item.name)
    for path in (vec_final, rec_final):
        if path.is_symlink():
            raise KnowledgeError(f"{path} is a symbolic link; links are not followed")
    manifest = read_manifest(db) if db.is_dir() else None
    exists = (manifest is not None and item.name in manifest) or vec_final.exists() or rec_final.exists()
    if exists and not replace:  # checked before the model is loaded
        raise KnowledgeError(f"collection {item.name} already exists in {db}; use --replace to build it again")
    documents = list(item.documents(root.kb_dir))
    metas = _check_records(agent, collection, documents)
    files = [Path(f) for f in (item.files(root.kb_dir) if item.files else [])]
    np = numpy_module()
    embedder = embedder or embedder_for(item.model, bool(config.get("knowledge.offline", True)))
    vectors = _embed(np, embedder, agent, collection, documents, progress)
    info = CollectionInfo(item.name, len(documents), item.model, int(vectors.shape[1]), item.space, now(),
                          tuple(file_entry(f) for f in files))
    db.mkdir(parents=True, exist_ok=True)
    tag = f".building-{os.getpid()}"
    vec_tmp, rec_tmp = db / f".{vec_final.name}{tag}", db / f".{rec_final.name}{tag}"
    try:
        for path in (vec_tmp, rec_tmp):
            path.unlink(missing_ok=True)
        with open(vec_tmp, "xb") as handle:
            np.save(handle, vectors, allow_pickle=False)
        _write_records(rec_tmp, info, documents, metas)
        _read_back(np, vec_tmp, rec_tmp, info)
        forget_store(db)  # this process stops using the old files
        os.replace(vec_tmp, vec_final)
        os.replace(rec_tmp, rec_final)
    finally:
        for path in (vec_tmp, rec_tmp):
            path.unlink(missing_ok=True)
    info = dataclasses.replace(info, store_files=(file_entry(vec_final), file_entry(rec_final)))
    write_entry(db, info)
    return info
