"""Build a declared collection from the agent's records (M1, ``champion kb build``).

The track supplies the records (``KnowledgeCollection.documents``: knowledge-base folder
-> ``Document``s) and the files they come from (``files``); this module embeds them with
the declared model, writes the Chroma collection and its ``MANIFEST.json`` entry.

Safety rules:
* only the user runs it, on the command line; no model tool can build;
* the database folder is inside the agent's checked knowledge root;
* an existing collection is replaced only with ``replace=True`` (``--replace``);
* a database without ``MANIFEST.json`` was not built by Champion (for example the old
  PyDyna_Agent database) and is refused unless ``allow_foreign=True``;
* ids must be unique; the count is checked after the build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from champion.knowledge.embedding import Embedder, embedder_for
from champion.knowledge.manifest import CollectionInfo, file_entry, now, read_manifest, write_entry
from champion.knowledge.registry import knowledge_spec
from champion.knowledge.root import KnowledgeError, resolve_root
from champion.knowledge.store import SQLITE, forget_store

BATCH = 256


def _exists(client: Any, name: str) -> bool:
    try:
        client.get_collection(name)
    except Exception:  # chromadb raises its own NotFound types
        return False
    return True


def build_collection(config: Any, agent: str, collection: str, *, replace: bool = False,
                     allow_foreign: bool = False, embedder: Embedder | None = None,
                     progress: Callable[[int, int], None] | None = None) -> CollectionInfo:
    spec = knowledge_spec(agent)
    item = spec.collection(collection)
    if item.documents is None:
        raise KnowledgeError(f"{agent}/{collection} does not say how to build it (no documents function)")
    root = resolve_root(config, agent, spec.config_section)
    db = root.db_dir(item.database)
    if db.is_symlink():
        raise KnowledgeError(f"{db} is a symbolic link; links are not followed")
    if (db / SQLITE).is_file() and read_manifest(db) is None and not allow_foreign:
        raise KnowledgeError(f"{db} has no MANIFEST.json, so it was not built by Champion (for example the old "
                             "agent's database); build into another folder (database vector_db_<name>) "
                             "or give --allow-foreign")
    documents = list(item.documents(root.kb_dir))
    if not documents:
        raise KnowledgeError(f"no records for {agent}/{collection} in {root.kb_dir}")
    ids = [d.id for d in documents]
    if len(set(ids)) != len(ids):
        raise KnowledgeError(f"{agent}/{collection}: {len(ids) - len(set(ids))} record ids are used twice")
    files = [Path(f) for f in (item.files(root.kb_dir) if item.files else [])]
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as exc:
        raise KnowledgeError(f"building needs chromadb and sentence-transformers (pip install -e '.[knowledge]'); "
                             f"{exc}") from None
    db.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db), settings=Settings(anonymized_telemetry=False))
    if _exists(client, item.name):  # checked before the model is loaded
        if not replace:
            raise KnowledgeError(f"collection {item.name} already exists in {db}; use --replace to build it again")
    embedder = embedder or embedder_for(item.model, bool(config.get("knowledge.offline", True)))
    forget_store(db)
    first = embedder.embed([documents[0].text])
    dimension = len(first[0])
    if replace and _exists(client, item.name):
        client.delete_collection(item.name)
    handle = client.create_collection(name=item.name, embedding_function=None, metadata={"hnsw:space": item.space})
    for start in range(0, len(documents), BATCH):
        part = documents[start:start + BATCH]
        vectors = embedder.embed([d.text for d in part])
        if any(len(v) != dimension for v in vectors):
            raise KnowledgeError(f"{agent}/{collection}: the model returned vectors of different lengths")
        handle.add(ids=[d.id for d in part], embeddings=vectors, documents=[d.text for d in part],
                   metadatas=[dict(d.metadata) or None for d in part])
        if progress is not None:
            progress(min(start + BATCH, len(documents)), len(documents))
    count = handle.count()
    if count != len(documents):
        raise KnowledgeError(f"{item.name} has {count} entries after the build, expected {len(documents)}")
    info = CollectionInfo(item.name, count, item.model, dimension, item.space, now(),
                          tuple(file_entry(f) for f in files))
    write_entry(db, info)
    return info
