"""Build one PyDyna search collection from the knowledge JSONL (track T1, T1.3b; stub until M1).

M1 will own indexing; until then this builds a Chroma collection with the same embedding model
the search uses (``search.EMBED_MODEL``) and the same document text and metadata as
``records.py``. It is meant for new collections in their own database folder (user, 24 Nov
2026: ``knowledge/pydyna/vector_db_dpf_core`` for ``pydpf_core``), so the database of the old
agent is not written::

    python -m champion.specialists.pydyna.build_index --collection pydpf_core \\
        --kb knowledge/pydyna/knowledge-base --db knowledge/pydyna/vector_db_dpf_core

* An existing collection is never replaced without ``--replace``; other collections in the
  folder are not touched.
* The folder of the main search database (``<kb>/../vector_db``) is refused unless
  ``--allow-main-db``.
* ``MANIFEST.json`` in the database folder: per collection the count, model, vector length,
  distance and the SHA-256 of the JSONL files it was built from.
* Distance ``l2`` by default: the collections of the old agent's database have no distance set,
  so they use Chroma's default ``l2`` (read on Linux, 24 Nov 2026); the same distance keeps
  the scores of all PyDyna searches comparable. ``--space`` gives another.

Needs chromadb and sentence-transformers (optional group ``pydyna``); the model comes from the
local Hugging Face cache (set ``HF_HUB_OFFLINE=1`` to never download).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

from champion.specialists.pydyna import records as rec
from champion.specialists.pydyna.search import EMBED_MODEL

BATCH = 256
SPACES = ("l2", "cosine", "ip")


class IndexBuildError(Exception):
    """The collection cannot be built; the message says why."""


def _post_files(kb: Path) -> list[Path]:
    return rec._shards_or_file(kb, rec.DPF_POST_SHARDS, rec.DPF_POST_FILE)


def _keyword_files(kb: Path) -> list[Path]:
    return [*rec.keyword_schema_files(kb), *([kb / rec.KW_REF_FILE] if (kb / rec.KW_REF_FILE).is_file() else [])]


# collection -> (documents of a knowledge folder, the files they come from)
COLLECTIONS: dict[str, tuple[Callable[[Path], list], Callable[[Path], list[Path]]]] = {
    rec.DPF_CORE_COLLECTION: (rec.dpf_core_documents, rec.dpf_core_files),
    rec.DPF_POST_COLLECTION: (rec.dpf_post_documents, _post_files),
    rec.API_COLLECTION: (rec.api_documents, rec.api_files),
    rec.KW_COLLECTION: (rec.keyword_documents, _keyword_files),
}


def _exists(client: Any, name: str) -> bool:
    try:
        client.get_collection(name)
    except Exception:  # chromadb raises its own NotFound / ValueError types
        return False
    return True


def build_collection(client: Any, embedding: Any, name: str, documents: list, replace: bool = False,
                     space: str = "l2", batch: int = BATCH, progress: Callable[[int, int], None] | None = None) -> int:
    """Create collection ``name`` with ``documents`` (``records.Document``); returns its count."""
    if space not in SPACES:
        raise IndexBuildError(f"space must be one of {', '.join(SPACES)}")
    if not documents:
        raise IndexBuildError(f"no records for {name}: generate the JSONL first (source_data.py)")
    if _exists(client, name):
        if not replace:
            raise IndexBuildError(f"collection {name} already exists; use --replace to build it again")
        client.delete_collection(name)
    collection = client.create_collection(name=name, embedding_function=embedding, metadata={"hnsw:space": space})
    for start in range(0, len(documents), batch):
        part = documents[start:start + batch]
        collection.add(ids=[d.id for d in part], documents=[d.text for d in part],
                       metadatas=[d.metadata for d in part])
        if progress is not None:
            progress(min(start + batch, len(documents)), len(documents))
    count = collection.count()
    if count != len(documents):
        raise IndexBuildError(f"{name} has {count} entries after the build, expected {len(documents)}")
    return count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(db: Path, name: str, count: int, model: str, dimension: int, space: str,
                   files: list[Path]) -> dict:
    """Add or replace the entry of one collection in ``<db>/MANIFEST.json``."""
    path = Path(db) / "MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"collections": {}}
    manifest.setdefault("collections", {})[name] = {
        "count": count, "model": model, "dimension": dimension, "space": space,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "files": [{"name": f.name, "bytes": f.stat().st_size, "sha256": _sha256(f)} for f in files]}
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def check_target(kb: Path, db: Path, allow_main_db: bool) -> None:
    main_db = (Path(kb).resolve().parent / "vector_db")
    if Path(db).resolve() == main_db and not allow_main_db:
        raise IndexBuildError(f"{db} is the main PyDyna search database; build into its own folder "
                              "(for example knowledge/pydyna/vector_db_dpf_core) or give --allow-main-db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m champion.specialists.pydyna.build_index",
                                     description="Build one PyDyna search collection from the knowledge JSONL.")
    parser.add_argument("--collection", required=True, choices=list(COLLECTIONS))
    parser.add_argument("--kb", type=Path, default=Path("knowledge/pydyna/knowledge-base"))
    parser.add_argument("--db", type=Path, required=True, help="database folder (created if missing)")
    parser.add_argument("--replace", action="store_true", help="build an existing collection again")
    parser.add_argument("--space", choices=SPACES, default="l2")
    parser.add_argument("--model", default=EMBED_MODEL)
    parser.add_argument("--allow-main-db", action="store_true")
    args = parser.parse_args(argv)
    documents_of, files_of = COLLECTIONS[args.collection]
    try:
        check_target(args.kb, args.db, args.allow_main_db)
        documents = documents_of(args.kb)
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        except ImportError as exc:
            raise IndexBuildError(f"needs chromadb and sentence-transformers (pip install -e '.[pydyna]'); {exc}") from None
        args.db.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(args.db), settings=Settings(anonymized_telemetry=False))
        if not args.replace and _exists(client, args.collection):  # before loading the model
            raise IndexBuildError(f"collection {args.collection} already exists; use --replace to build it again")
        embedding = SentenceTransformerEmbeddingFunction(model_name=args.model, device="cpu")
        dimension = len(embedding(["dimension check"])[0])
        print(f"{args.collection}: {len(documents)} records from {args.kb}, model {args.model} ({dimension})")
        count = build_collection(client, embedding, args.collection, documents, args.replace, args.space,
                                 progress=lambda done, total: print(f"  {done}/{total}", end="\r", flush=True))
        write_manifest(args.db, args.collection, count, args.model, dimension, args.space, files_of(args.kb))
    except (IndexBuildError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    print(f"\n{args.collection}: {count} entries in {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
