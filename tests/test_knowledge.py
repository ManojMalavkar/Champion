"""M1 knowledge layer with Champion's own store (CR-4): root, manifest, store, search,
tools, builder, status, CLI.

A fake embedder (4 numbers per text: counts of four words) replaces the model. Nothing
here needs Chroma.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest
from agents import RunContextWrapper

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.knowledge import builder, embedding, store as store_mod
from champion.knowledge.builder import build_collection
from champion.knowledge.manifest import MANIFEST, CollectionInfo, FileEntry, file_entry, read_manifest, write_entry
from champion.knowledge.registry import (Document, KnowledgeCollection, KnowledgeSpec, register_knowledge,
                                         unregister_knowledge)
from champion.knowledge.root import KnowledgeError, resolve_root
from champion.knowledge.search import format_hits, lookup, search
from champion.knowledge.status import FAIL, OK, WARN, knowledge_status
from champion.knowledge.store import DatabaseChanged, fingerprint, records_path, store_for, vectors_path
from champion.knowledge.tools import make_lookup_tool, make_search_tool
from champion.policy.tool_policy import rule_for

MODEL = "test/fake-model"
WORDS = ("material", "contact", "section", "energy")


class FakeEmbedder:
    model = MODEL

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[float(text.lower().count(word)) for word in WORDS] for text in texts]


def fake_vector(text):
    return FakeEmbedder().embed([text])[0]


RECORDS = [
    Document("mat024", "material material piecewise plasticity", {"category": "material", "source_path": "kw.jsonl",
                                                                  "chunk_index": 0}),
    Document("mat020", "material rigid", {"category": "material", "source_path": "kw.jsonl", "chunk_index": 1}),
    Document("cont", "contact automatic surface", {"category": "contact", "source_path": "kw.jsonl", "chunk_index": 2}),
    Document("test1", "material in a test file", {"category": "material", "source_path": "tests/test_x.py"}),
    Document("sec", "section force energy", {"category": "section", "source_path": "src/sec.py", "note": None}),
]


def _files(kb: Path) -> list[Path]:
    return [kb / "kw.jsonl"]


def make_project(tmp_path: Path, extra: str = "") -> tuple:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "config.yaml").write_text("llm:\n  model_id: m\n" + extra, encoding="utf-8")
    kb = tmp_path / "knowledge" / "demo" / "knowledge-base"
    kb.mkdir(parents=True)
    (kb / "kw.jsonl").write_text("\n".join(json.dumps({"id": r.id}) for r in RECORDS) + "\n")
    return load_config(tmp_path / "config" / "config.yaml", env={}, cwd=tmp_path), kb


@pytest.fixture
def demo(tmp_path):
    fake = FakeEmbedder()
    embedding.set_embedder(MODEL, fake)
    spec = KnowledgeSpec("demo", (
        KnowledgeCollection("demo_keywords", MODEL, documents=lambda kb: RECORDS, files=_files,
                            keep=lambda hit: not hit.source.startswith("tests/")),
        KnowledgeCollection("demo_extra", MODEL, database="index_extra", documents=lambda kb: RECORDS[:2]),
    ))
    register_knowledge(spec)
    config, kb = make_project(tmp_path)
    yield config, kb, fake
    unregister_knowledge("demo")
    embedding.set_embedder(MODEL, None)
    store_mod.forget_store()


def store_dir(kb: Path, name: str = "index") -> Path:
    return kb.parent / name


def relist(db: Path, name: str, **changes) -> None:
    """Write the manifest entry again with the files as they are now (for tests that change a file)."""
    info = read_manifest(db)[name]
    files = (file_entry(vectors_path(db, name)), file_entry(records_path(db, name)))
    write_entry(db, CollectionInfo(**{**info.__dict__, "store_files": files, **changes}))


# ------------------------------------------------------------------ root

def test_root_default_and_refusals(tmp_path):
    config, _ = make_project(tmp_path)
    root = resolve_root(config, "demo")
    assert root.path == tmp_path.resolve() / "knowledge" / "demo"
    assert root.kb_dir.name == "knowledge-base" and root.db_dir().name == "index"
    assert root.db_dir("index_dpf_core").name == "index_dpf_core"
    for bad in ("../elsewhere", "vector_db", "vector_db_dpf_core"):  # old Chroma folders cannot be a store
        with pytest.raises(KnowledgeError, match="index or index_<name>"):
            root.db_dir(bad)
    outside, _ = make_project(tmp_path / "p2", "demo:\n  knowledge_root: /tmp\n")
    outside.data["demo"] = {"knowledge_root": str(tmp_path)}  # the project's parent
    with pytest.raises(KnowledgeError, match="outside the project folder"):
        resolve_root(outside, "demo")
    target = tmp_path / "real"
    target.mkdir()
    (tmp_path / "knowledge" / "linked").symlink_to(target)
    with pytest.raises(KnowledgeError, match="symbolic link"):
        resolve_root(config, "linked")
    with pytest.raises(KnowledgeError, match="not valid"):
        resolve_root(config, "Bad Name")


def test_root_from_config_key_and_folder_lists(tmp_path):
    config, kb = make_project(tmp_path)
    for name in ("index", "index_dpf_core", "vector_db", "vector_db_dpf_core", "other"):
        (kb.parent / name).mkdir()
    root = resolve_root(config, "demo")
    assert root.databases() == ["index", "index_dpf_core"]
    assert root.old_databases() == ["vector_db", "vector_db_dpf_core"]
    config.data["demo_section"] = {"knowledge_root": "data/kb_demo"}
    assert resolve_root(config, "demo", "demo_section").path == tmp_path.resolve() / "data" / "kb_demo"


# ------------------------------------------------------------------ manifest

def test_manifest_round_trip_store_files_and_old_format(tmp_path):
    assert read_manifest(tmp_path) is None
    write_entry(tmp_path, CollectionInfo("a_col", 3, MODEL, 4, "l2", "now",
                                         store_files=(FileEntry("a_col.npy", 10, "a" * 64),)))
    write_entry(tmp_path, CollectionInfo("b_col", 5, MODEL, 4, "cosine"))
    info = read_manifest(tmp_path)
    assert sorted(info) == ["a_col", "b_col"] and info["b_col"].space == "cosine"
    assert info["a_col"].store_files == (FileEntry("a_col.npy", 10, "a" * 64),)
    assert json.loads((tmp_path / MANIFEST).read_text())["format"] == 2
    # format 1 (the first M1 builder and the PyDyna track's build_index.py, no "format" key) is read as well
    (tmp_path / MANIFEST).write_text(json.dumps({"collections": {"pydpf_core": {
        "count": 13014, "model": "BAAI/bge-small-en-v1.5", "dimension": 384, "space": "l2", "built_at": "x",
        "files": [{"name": "pydpf_core_source.jsonl", "bytes": 1, "sha256": "0" * 64}]}}}))
    old = read_manifest(tmp_path)["pydpf_core"]
    assert old.count == 13014 and old.store_files == ()


@pytest.mark.parametrize("content, message", [
    ("not json", "cannot be read"),
    ('{"collections": []}', "must contain"),
    ('{"format": 9, "collections": {}}', "format 9"),
    ('{"collections": {"c": {"count": -1, "model": "m", "dimension": 4}}}', "count"),
    ('{"collections": {"c": {"count": 1, "model": "m", "dimension": 4, "space": "manhattan"}}}', "space"),
    ('{"collections": {"c": {"count": 1, "model": "m", "dimension": 4, "files": [{"name": "../x"}]}}}', "file entries"),
    ('{"collections": {"c": {"count": 1, "model": "m", "dimension": 4, "store_files": "x.npy"}}}', "must be lists"),
])
def test_bad_manifest_is_refused(tmp_path, content, message):
    (tmp_path / MANIFEST).write_text(content)
    with pytest.raises(KnowledgeError, match=message):
        read_manifest(tmp_path)


# ------------------------------------------------------------------ 1. build writes the two files

def test_build_writes_both_files_and_they_read_back_equal_to_the_records(demo):
    config, kb, _ = demo
    info = build_collection(config, "demo", "demo_keywords")
    assert (info.count, info.dimension, info.space, info.model) == (5, 4, "l2", MODEL)
    assert info.files[0].name == "kw.jsonl" and len(info.files[0].sha256) == 64
    db = store_dir(kb)
    assert sorted(os.listdir(db)) == [MANIFEST, "demo_keywords.npy", "demo_keywords.sqlite3"]  # no temporary files
    assert [f.name for f in read_manifest(db)["demo_keywords"].store_files] == ["demo_keywords.npy",
                                                                                 "demo_keywords.sqlite3"]
    vectors = np.load(vectors_path(db, "demo_keywords"), allow_pickle=False)
    assert vectors.dtype == np.float32 and vectors.shape == (5, 4)
    assert vectors.tolist() == [fake_vector(r.text) for r in RECORDS]
    con = sqlite3.connect(records_path(db, "demo_keywords"))
    rows = con.execute("select row, id, document, metadata from records order by row").fetchall()
    meta = dict(con.execute("select key, value from info"))
    con.close()
    assert [(r, i, d) for r, i, d, _ in rows] == [(n, rec.id, rec.text) for n, rec in enumerate(RECORDS)]
    assert json.loads(rows[4][3]) == {"category": "section", "source_path": "src/sec.py"}  # None left out
    assert meta == {"format": "1", "model": MODEL, "dimension": "4", "space": "l2", "count": "5"}


# ------------------------------------------------------------------ 2. search equals brute force

def test_build_then_search_with_distances_sources_and_filters(demo):
    config, kb, fake = demo
    build_collection(config, "demo", "demo_keywords")
    hits = search(config, "demo", "demo_keywords", "material", n_results=3)
    # query (1,0,0,0): mat020 and test1 at 0 (test1 dropped by the track's keep), mat024 at 1, cont at 2
    assert [(h.id, h.distance) for h in hits] == [("mat020", 0.0), ("mat024", 1.0), ("cont", 2.0)]
    assert all(h.score == round(1 - h.distance, 3) for h in hits)
    assert hits[0].source == "kw.jsonl" and hits[0].chunk == "1"
    only = search(config, "demo", "demo_keywords", "material contact", n_results=5, filters={"category": "contact"})
    assert [h.id for h in only] == ["cont"]
    assert search(config, "demo", "demo_keywords", "material", filters={"category": "nothing"}) == []
    exact = lookup(config, "demo", "demo_keywords", filters={"category": "section"})
    assert [h.id for h in exact] == ["sec"] and exact[0].distance is None
    assert [h.id for h in lookup(config, "demo", "demo_keywords", ids=["cont", "mat024", "nobody"])] == ["mat024", "cont"]
    assert lookup(config, "demo", "demo_keywords", ids=["cont"], filters={"category": "material"}) == []
    text = format_hits(hits, 10, "demo/demo_keywords")
    assert "score" in text and "distance" in text and "source kw.jsonl chunk" in text and "more characters" in text


@pytest.mark.parametrize("space", ["l2", "cosine", "ip"])
def test_search_equals_brute_force_in_every_space_with_and_without_filters(demo, space):
    config, _, _ = demo
    rng = np.random.default_rng(7)
    documents = []
    for n in range(40):
        words = " ".join(w for w in WORDS for _ in range(int(rng.integers(0, 4))))
        documents.append(Document(f"doc{n:02d}", words or "empty", {"category": "even" if n % 2 == 0 else "odd",
                                                                    "number": n, "flag": n % 3 == 0}))
    register_knowledge(KnowledgeSpec("brute", (KnowledgeCollection("brute_docs", MODEL, space=space,
                                                                   documents=lambda kb: documents),)))
    try:
        build_collection(config, "brute", "brute_docs")
        matrix = np.array([fake_vector(d.text) for d in documents], dtype=np.float64)
        for text in ("material", "contact energy", "section section material", "nothing"):
            q = np.array(fake_vector(text), dtype=np.float64)
            if space == "l2":
                reference = ((matrix - q) ** 2).sum(axis=1)
            elif space == "cosine":
                norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(q)
                reference = 1 - np.divide(matrix @ q, norms, out=np.zeros(len(matrix)), where=norms > 0)
            else:
                reference = 1 - matrix @ q
            for filters, allowed in ((None, range(40)), ({"category": "odd"}, range(1, 40, 2)),
                                     ({"flag": True}, range(0, 40, 3)), ({"number": 7}, [7])):
                hits = search(config, "brute", "brute_docs", text, n_results=10, filters=filters)
                rows = list(allowed)
                expected = sorted(reference[rows])[:10]
                assert [h.distance for h in hits] == pytest.approx(expected, abs=1e-5)
                for hit in hits:
                    row = int(hit.id[3:])
                    assert row in rows and hit.distance == pytest.approx(reference[row], abs=1e-5)
                    assert hit.document == documents[row].text and hit.metadata["number"] == row
    finally:
        unregister_knowledge("brute")


def test_filters_work_without_sqlite_json_functions(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    wanted = [h.id for h in search(config, "demo", "demo_keywords", "material", filters={"category": "material"})]

    class NoJson:  # an SQLite build without json_extract
        def __init__(self, con):
            self.con = con

        def execute(self, sql, *args):
            if "json_extract" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return self.con.execute(sql, *args)

        def close(self):
            self.con.close()

    item = store_for(store_dir(kb), True)._collections["demo_keywords"]
    item.con = NoJson(item.con)
    assert [h.id for h in search(config, "demo", "demo_keywords", "material",
                                 filters={"category": "material"})] == wanted == ["mat020", "mat024"]
    with pytest.raises(KnowledgeError, match="letters, digits and _"):
        search(config, "demo", "demo_keywords", "material", filters={"bad key'": "x"})


# ------------------------------------------------------------------ 3. read only

def test_search_leaves_every_file_unchanged_and_needs_no_write_access(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    store_mod.forget_store()
    db = store_dir(kb)
    before = fingerprint(db, True)
    files = [db / name for name in os.listdir(db)]
    try:
        for path in files:
            path.chmod(0o444)
        db.chmod(0o555)
        assert [h.id for h in search(config, "demo", "demo_keywords", "contact")][0] == "cont"
        assert [h.id for h in lookup(config, "demo", "demo_keywords", ids=["sec"])] == ["sec"]
        assert [line.status for line in knowledge_status(config, "demo", verify=True)
                if line.name == "Collection demo/demo_keywords"] == [OK]
    finally:
        db.chmod(0o755)
        for path in files:
            path.chmod(0o644)
    store_mod.forget_store()  # opened again with the folder and files writable: SQLite itself must refuse
    assert search(config, "demo", "demo_keywords", "contact")[0].id == "cont"
    con = store_for(db, True)._collections["demo_keywords"].con
    for statement in ("create table x (a)", "delete from records", "update info set value = 'x'"):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute(statement)
    assert search(config, "demo", "demo_keywords", "contact")[0].id == "cont"
    assert fingerprint(db, True) == before
    assert sorted(os.listdir(db)) == sorted(p.name for p in files)  # no journal or lock files


def test_changed_database_stops_the_store(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    store_mod.forget_store()
    search(config, "demo", "demo_keywords", "contact")
    (store_dir(kb) / "extra.bin").write_bytes(b"someone wrote here")
    with pytest.raises(DatabaseChanged, match="changed while Champion had it open"):
        search(config, "demo", "demo_keywords", "contact")
    with pytest.raises(DatabaseChanged):  # stays stopped
        search(config, "demo", "demo_keywords", "contact")


# ------------------------------------------------------------------ 4. refusals

def _tamper_vectors(db, array, allow_pickle=False):
    with open(vectors_path(db, "demo_keywords"), "wb") as handle:
        np.save(handle, array, allow_pickle=allow_pickle)


@pytest.mark.parametrize("case, message", [
    ("object", "cannot be read as a vector file"),
    ("float64", "expected float32 vectors of shape \\(5, 4\\)"),
    ("shape", "expected float32 vectors of shape \\(5, 4\\)"),
    ("nan", "not finite"),
    ("rows", "does not match"),
    ("info", "does not match: model"),
    ("unlisted", "not the file MANIFEST.json lists"),
    ("missing", "is missing"),
    ("old_entry", "lists no store files"),
])
def test_broken_store_files_are_refused(demo, case, message):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    store_mod.forget_store()
    db = store_dir(kb)
    good = np.load(vectors_path(db, "demo_keywords"))
    if case == "object":
        _tamper_vectors(db, np.array([object()] * 5), allow_pickle=True)
    elif case == "float64":
        _tamper_vectors(db, good.astype(np.float64))
    elif case == "shape":
        _tamper_vectors(db, good[:4])
    elif case == "nan":
        bad = good.copy()
        bad[2, 1] = np.nan
        _tamper_vectors(db, bad)
    elif case in ("rows", "info"):
        con = sqlite3.connect(records_path(db, "demo_keywords"))
        con.execute("delete from records where row = 4" if case == "rows"
                    else "update info set value = 'other/model' where key = 'model'")
        con.commit()
        con.close()
    if case not in ("unlisted", "missing", "old_entry"):
        relist(db, "demo_keywords")  # the manifest lists the changed files, so the file checks run
    if case == "unlisted":
        bad = good.copy()
        bad[0, 0] = 9.0  # same size, other content: found by the SHA-256
        _tamper_vectors(db, bad)
    if case == "missing":
        vectors_path(db, "demo_keywords").unlink()
    if case == "old_entry":
        relist(db, "demo_keywords")
        data = json.loads((db / MANIFEST).read_text())
        data["collections"]["demo_keywords"].pop("store_files")
        (db / MANIFEST).write_text(json.dumps(data))
    with pytest.raises(KnowledgeError, match=message):
        search(config, "demo", "demo_keywords", "contact")


def test_object_vectors_are_never_unpickled(demo, monkeypatch):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    store_mod.forget_store()
    db = store_dir(kb)
    marker = kb.parent / "unpickled"
    _tamper_vectors(db, np.array([Exploit(str(marker))], dtype=object), allow_pickle=True)
    relist(db, "demo_keywords")
    with pytest.raises(KnowledgeError, match="cannot be read as a vector file"):
        search(config, "demo", "demo_keywords", "contact")
    assert not marker.exists()


class Exploit:
    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return (Path.touch, (Path(self.path),))


def test_store_folder_through_a_link_is_refused(demo, tmp_path):
    config, kb, _ = demo
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (kb.parent / "index").symlink_to(elsewhere)
    with pytest.raises(KnowledgeError, match="symbolic link"):
        build_collection(config, "demo", "demo_keywords")


def test_model_and_dimension_must_match_the_manifest(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    db = store_dir(kb)
    data = json.loads((db / MANIFEST).read_text())
    data["collections"]["demo_keywords"]["model"] = "other/model"
    (db / MANIFEST).write_text(json.dumps(data))
    with pytest.raises(KnowledgeError, match="was built with other/model"):
        search(config, "demo", "demo_keywords", "contact")
    data["collections"]["demo_keywords"].update(model=MODEL, dimension=8)
    (db / MANIFEST).write_text(json.dumps(data))
    with pytest.raises(KnowledgeError, match="length 4"):
        search(config, "demo", "demo_keywords", "contact")


def test_missing_database_says_how_to_build(demo):
    config, _, _ = demo
    with pytest.raises(KnowledgeError, match="champion kb build"):
        search(config, "demo", "demo_keywords", "contact")
    with pytest.raises(KnowledgeError, match="no knowledge base is declared for nobody"):
        search(config, "nobody", "x_col", "contact")
    with pytest.raises(KnowledgeError, match="empty"):
        search(config, "demo", "demo_keywords", "  ")
    build_collection(config, "demo", "demo_keywords")  # demo_extra lives in index_extra, still not built
    with pytest.raises(KnowledgeError, match="champion kb build --agent demo --collection demo_extra"):
        search(config, "demo", "demo_extra", "contact")


def test_registry_rejects_bad_specs():
    with pytest.raises(ValueError, match="collection name"):
        register_knowledge(KnowledgeSpec("bad", (KnowledgeCollection("x", MODEL),)))
    for database in ("../db", "vector_db", "vector_db_dpf_core"):
        with pytest.raises(ValueError, match="database must be index or index_<name>"):
            register_knowledge(KnowledgeSpec("bad", (KnowledgeCollection("good_name", MODEL, database=database),)))
    with pytest.raises(ValueError, match="space"):
        register_knowledge(KnowledgeSpec("bad", (KnowledgeCollection("good_name", MODEL, space="manhattan"),)))


# ------------------------------------------------------------------ 5. build rules

def test_build_refusals_and_replace(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    with pytest.raises(KnowledgeError, match="already exists"):
        build_collection(config, "demo", "demo_keywords")
    assert build_collection(config, "demo", "demo_keywords", replace=True).count == 5
    # files without a manifest entry also count as existing
    (store_dir(kb, "index_extra")).mkdir()
    vectors_path(store_dir(kb, "index_extra"), "demo_extra").write_bytes(b"left over")
    with pytest.raises(KnowledgeError, match="already exists"):
        build_collection(config, "demo", "demo_extra")
    assert build_collection(config, "demo", "demo_extra", replace=True).count == 2


def test_bad_records_are_refused(demo):
    config, _, _ = demo
    for documents, message in (
            (RECORDS + RECORDS[:1], "used twice"),
            ([Document("", "text")], "needs an id"),
            ([Document("a", "text", {"list": [1, 2]})], "metadata list"),
            ([], "no records")):
        unregister_knowledge("demo")
        register_knowledge(KnowledgeSpec("demo", (KnowledgeCollection("demo_keywords", MODEL,
                                                                      documents=lambda kb, d=documents: d),)))
        with pytest.raises(KnowledgeError, match=message):
            build_collection(config, "demo", "demo_keywords")


def test_bad_vectors_from_the_model_are_refused(demo):
    config, _, _ = demo

    class Ragged(FakeEmbedder):
        def embed(self, texts):
            return [[1.0] * (2 + n % 2) for n, _ in enumerate(texts)]

    class NotFinite(FakeEmbedder):
        def embed(self, texts):
            return [[float("inf"), 0.0, 0.0, 0.0] for _ in texts]

    with pytest.raises(KnowledgeError, match="different lengths"):
        build_collection(config, "demo", "demo_keywords", embedder=Ragged())
    with pytest.raises(KnowledgeError, match="not finite"):
        build_collection(config, "demo", "demo_keywords", embedder=NotFinite())


@pytest.mark.parametrize("stage", ["embedding", "writing"])
def test_a_failed_build_leaves_the_previous_files_unchanged(demo, monkeypatch, stage):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    db = store_dir(kb)
    before = fingerprint(db, True)
    monkeypatch.setattr(builder, "BATCH", 2)

    class Breaks(FakeEmbedder):
        batches = 0

        def embed(self, texts):
            self.batches += 1
            if self.batches == 2:  # the second batch of three
                raise RuntimeError("model crashed")
            return super().embed(texts)

    if stage == "writing":
        def broken_read_back(*args):
            raise KnowledgeError("the written files do not match; nothing was replaced")
        monkeypatch.setattr(builder, "_read_back", broken_read_back)
        with pytest.raises(KnowledgeError, match="nothing was replaced"):
            build_collection(config, "demo", "demo_keywords", replace=True)
    else:
        with pytest.raises(RuntimeError, match="model crashed"):
            build_collection(config, "demo", "demo_keywords", replace=True, embedder=Breaks())
    assert fingerprint(db, True) == before  # previous files as they were, no temporary files left
    assert [h.id for h in search(config, "demo", "demo_keywords", "contact")][0] == "cont"


def test_build_stops_an_open_store_of_the_folder(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    assert search(config, "demo", "demo_keywords", "contact")[0].id == "cont"
    build_collection(config, "demo", "demo_keywords", replace=True)
    assert search(config, "demo", "demo_keywords", "contact")[0].id == "cont"  # opened again, not stale


# ------------------------------------------------------------------ embedding

def test_missing_model_gives_clear_error(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    embedder = embedding.SentenceTransformerEmbedder("nobody/no-such-model-xyz", offline=True)
    with pytest.raises(KnowledgeError, match="sentence-transformers|not in the local Hugging Face cache"):
        embedder.embed(["text"])


# ------------------------------------------------------------------ tools

def _ctx(config):
    return RunContextWrapper(ChampionContext.from_config(config))


def test_search_and_lookup_tools(demo):
    config, _, _ = demo
    build_collection(config, "demo", "demo_keywords")
    tool = make_search_tool(name="search_demo", agent="demo", collection="demo_keywords",
                            description="Search the demo keywords.", filters={"category": "category"})
    assert rule_for("search_demo").right == "none"
    schema = tool.params_json_schema
    assert schema["required"] == ["query", "n_results", "category"] and schema["additionalProperties"] is False
    out = asyncio.run(tool.on_invoke_tool(_ctx(config), json.dumps({"query": "contact", "n_results": None,
                                                                     "category": "contact"})))
    assert out.startswith("demo/demo_keywords: 1 result(s)") and "id cont" in out
    look = make_lookup_tool(name="lookup_demo", agent="demo", collection="demo_keywords",
                            description="Exact demo entry.", key_arg="category", key_field="category")
    out = asyncio.run(look.on_invoke_tool(_ctx(config), json.dumps({"category": "section"})))
    assert "id sec" in out and "distance" not in out
    out = asyncio.run(look.on_invoke_tool(_ctx(config), json.dumps({"category": ""})))
    assert out.startswith("ERROR:")


def test_search_tool_reports_missing_database(demo):
    config, _, _ = demo
    tool = make_search_tool(name="search_demo_missing", agent="demo", collection="demo_keywords",
                            description="Search.")
    out = asyncio.run(tool.on_invoke_tool(_ctx(config), json.dumps({"query": "contact", "n_results": 2})))
    assert out.startswith("ERROR:") and "champion kb build" in out
    assert asyncio.run(tool.on_invoke_tool(_ctx(config), "not json")).startswith("ERROR:")


# ------------------------------------------------------------------ 6. status and CLI

def test_status_without_data_warns_and_lists_old_chroma_folders_as_backups(demo):
    config, kb, _ = demo
    old = kb.parent / "vector_db"
    old.mkdir()
    (old / "chroma.sqlite3").write_bytes(b"old chroma")
    before = fingerprint(old, True)
    lines = {line.name: line for line in knowledge_status(config, verify=True)}
    assert lines["Knowledge demo"].status == OK
    assert lines["Knowledge demo/index"].status == WARN and "champion kb build" in lines["Knowledge demo/index"].detail
    assert lines["Knowledge demo/vector_db"].status == OK
    assert "old Chroma database (backup, not used)" in lines["Knowledge demo/vector_db"].detail
    assert lines[f"Embedding {MODEL}"].status in (OK, WARN)
    assert knowledge_status(config, "nobody")[0].status == FAIL
    build_collection(config, "demo", "demo_keywords")
    assert fingerprint(old, True) == before  # the backup is never opened or written


def test_status_after_build_and_count_or_file_problems(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    lines = {line.name: line for line in knowledge_status(config, verify=True)}
    line = lines["Collection demo/demo_keywords"]
    assert line.status == OK and "5 entries" in line.detail and "1 source file(s) match" in line.detail
    assert "(4, l2)" in line.detail
    assert lines["Knowledge demo/index_extra"].status == WARN  # demo_extra not built: its folder has no store
    build_collection(config, "demo", "demo_extra")
    (kb / "kw.jsonl").write_text("changed\n")
    store_mod.forget_store()
    line = {l.name: l for l in knowledge_status(config, verify=True)}["Collection demo/demo_keywords"]
    assert line.status == FAIL and "changed since the build" in line.detail
    db = store_dir(kb)
    data = json.loads((db / MANIFEST).read_text())
    data["collections"]["demo_keywords"]["count"] = 99
    (db / MANIFEST).write_text(json.dumps(data))
    store_mod.forget_store()
    line = {l.name: l for l in knowledge_status(config)}["Collection demo/demo_keywords"]
    assert line.status == FAIL and "shape (99, 4)" in line.detail
    data["collections"].pop("demo_keywords")
    (db / MANIFEST).write_text(json.dumps(data))
    line = {l.name: l for l in knowledge_status(config)}["Collection demo/demo_keywords"]
    assert line.status == WARN and "not built yet" in line.detail


def test_cli_kb_status_and_build(demo, capsys, monkeypatch):
    from champion import cli

    config, _, _ = demo
    monkeypatch.setattr(cli, "load_config", lambda path=None: config)
    assert cli.main(["kb", "build"]) == 2
    assert cli.main(["kb", "build", "--agent", "demo", "--collection", "demo_keywords"]) == 0
    assert "demo/demo_keywords: 5 entries" in capsys.readouterr().out
    assert cli.main(["kb", "build", "--agent", "demo", "--collection", "demo_keywords"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["kb", "build", "--agent", "demo", "--collection", "demo_keywords", "--replace"]) == 0
    capsys.readouterr()
    store_mod.forget_store()
    assert cli.main(["kb", "status", "--agent", "demo", "--verify"]) == 0
    assert "Collection demo/demo_keywords" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # removed with CR-4: a store folder cannot hold a foreign database
        cli.main(["kb", "build", "--agent", "demo", "--allow-foreign"])


# ------------------------------------------------------------------ 7. no Chroma

def test_build_search_and_status_never_import_chromadb(demo, monkeypatch):
    config, _, _ = demo
    monkeypatch.setitem(sys.modules, "chromadb", None)  # any import of chromadb now raises ImportError
    build_collection(config, "demo", "demo_keywords")
    assert search(config, "demo", "demo_keywords", "contact")[0].id == "cont"
    assert lookup(config, "demo", "demo_keywords", ids=["sec"])[0].id == "sec"
    assert all(line.status != FAIL for line in knowledge_status(config, "demo", verify=True))
    sources = Path(store_mod.__file__).parent.glob("*.py")
    assert not [p.name for p in sources if "import chromadb" in p.read_text(encoding="utf-8")]


# ------------------------------------------------------------------ config (option 3b)

def test_old_pydyna_file_keys_still_work_with_a_warning(tmp_path):
    config, _ = make_project(tmp_path, "pydyna:\n  large_file_bytes: 2048\n  model_timeout_seconds: 30\n")
    assert config.get("files.large_file_bytes") == 2048 and config.get("files.model_timeout_seconds") == 30
    assert any("'pydyna.large_file_bytes' is now 'files.large_file_bytes'" in w for w in config.warnings)
    assert not any("Unknown config key 'pydyna'" in w for w in config.warnings)


def test_new_files_key_wins_over_the_old_name(tmp_path):
    config, _ = make_project(tmp_path, "files:\n  large_file_bytes: 4096\npydyna:\n  large_file_bytes: 2048\n")
    assert config.get("files.large_file_bytes") == 4096
    assert any("is ignored" in w for w in config.warnings)
