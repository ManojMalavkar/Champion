"""M1 knowledge layer: root, manifest, read-only store, search, tools, builder, status, CLI.

A fake embedder (4 numbers per text) replaces the model; Chroma tests are skipped when
chromadb is not installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from agents import RunContextWrapper

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.knowledge import embedding, store as store_mod
from champion.knowledge.builder import build_collection
from champion.knowledge.manifest import MANIFEST, CollectionInfo, read_manifest, write_entry
from champion.knowledge.registry import (Document, KnowledgeCollection, KnowledgeSpec, register_knowledge,
                                         unregister_knowledge)
from champion.knowledge.root import KnowledgeError, resolve_root
from champion.knowledge.search import format_hits, lookup, search
from champion.knowledge.status import FAIL, OK, WARN, knowledge_status
from champion.knowledge.store import DatabaseChanged, fingerprint
from champion.knowledge.tools import make_lookup_tool, make_search_tool
from champion.policy.tool_policy import rule_for

HAS_CHROMA = importlib.util.find_spec("chromadb") is not None
needs_chroma = pytest.mark.skipif(not HAS_CHROMA, reason="chromadb not installed")

MODEL = "test/fake-model"
WORDS = ("material", "contact", "section", "energy")


class FakeEmbedder:
    model = MODEL

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[float(text.lower().count(word)) for word in WORDS] for text in texts]


RECORDS = [
    Document("mat024", "material material piecewise plasticity", {"category": "material", "source_path": "kw.jsonl",
                                                                  "chunk_index": 0}),
    Document("mat020", "material rigid", {"category": "material", "source_path": "kw.jsonl", "chunk_index": 1}),
    Document("cont", "contact automatic surface", {"category": "contact", "source_path": "kw.jsonl", "chunk_index": 2}),
    Document("test1", "material in a test file", {"category": "material", "source_path": "tests/test_x.py"}),
    Document("sec", "section force energy", {"category": "section", "source_path": "src/sec.py"}),
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
        KnowledgeCollection("demo_extra", MODEL, database="vector_db_extra", documents=lambda kb: RECORDS[:2]),
    ))
    register_knowledge(spec)
    config, kb = make_project(tmp_path)
    yield config, kb, fake
    unregister_knowledge("demo")
    embedding.set_embedder(MODEL, None)
    store_mod.forget_store()


# ------------------------------------------------------------------ root

def test_root_default_and_refusals(tmp_path):
    config, _ = make_project(tmp_path)
    root = resolve_root(config, "demo")
    assert root.path == tmp_path.resolve() / "knowledge" / "demo"
    assert root.kb_dir.name == "knowledge-base" and root.db_dir().name == "vector_db"
    assert root.db_dir("vector_db_dpf_core").name == "vector_db_dpf_core"
    with pytest.raises(KnowledgeError, match="vector_db or vector_db_<name>"):
        root.db_dir("../elsewhere")
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


def test_root_from_config_key(tmp_path):
    config, _ = make_project(tmp_path)
    config.data["demo_section"] = {"knowledge_root": "data/kb_demo"}
    root = resolve_root(config, "demo", "demo_section")
    assert root.path == tmp_path.resolve() / "data" / "kb_demo"


# ------------------------------------------------------------------ manifest

def test_manifest_round_trip_and_track_format(tmp_path):
    assert read_manifest(tmp_path) is None
    write_entry(tmp_path, CollectionInfo("a_col", 3, MODEL, 4, "l2", "now"))
    write_entry(tmp_path, CollectionInfo("b_col", 5, MODEL, 4, "cosine"))
    info = read_manifest(tmp_path)
    assert sorted(info) == ["a_col", "b_col"] and info["b_col"].space == "cosine"
    # the PyDyna track's build_index.py format (no "format" key) is read as well
    (tmp_path / MANIFEST).write_text(json.dumps({"collections": {"pydpf_core": {
        "count": 13014, "model": "BAAI/bge-small-en-v1.5", "dimension": 384, "space": "l2", "built_at": "x",
        "files": [{"name": "pydpf_core_source.jsonl", "bytes": 1, "sha256": "0" * 64}]}}}))
    assert read_manifest(tmp_path)["pydpf_core"].count == 13014


@pytest.mark.parametrize("content, message", [
    ("not json", "cannot be read"),
    ('{"collections": []}', "must contain"),
    ('{"format": 9, "collections": {}}', "format 9"),
    ('{"collections": {"c": {"count": -1, "model": "m", "dimension": 4}}}', "count"),
    ('{"collections": {"c": {"count": 1, "model": "m", "dimension": 4, "space": "manhattan"}}}', "space"),
    ('{"collections": {"c": {"count": 1, "model": "m", "dimension": 4, "files": [{"name": "../x"}]}}}', "file entries"),
])
def test_bad_manifest_is_refused(tmp_path, content, message):
    (tmp_path / MANIFEST).write_text(content)
    with pytest.raises(KnowledgeError, match=message):
        read_manifest(tmp_path)


# ------------------------------------------------------------------ build, search, lookup

@needs_chroma
def test_build_then_search_with_distances_sources_and_filters(demo):
    config, kb, fake = demo
    info = build_collection(config, "demo", "demo_keywords")
    assert (info.count, info.dimension, info.space, info.model) == (5, 4, "l2", MODEL)
    assert info.files[0].name == "kw.jsonl" and len(info.files[0].sha256) == 64
    hits = search(config, "demo", "demo_keywords", "material", n_results=3)
    assert [h.id for h in hits][:2] == ["mat020", "mat024"] or hits[0].id in ("mat020", "mat024")
    assert all(h.distance is not None and h.score == round(1 - h.distance, 3) for h in hits)
    assert "test1" not in [h.id for h in hits]  # the track's keep function
    assert hits[0].source == "kw.jsonl" and hits[0].chunk in ("0", "1")
    only = search(config, "demo", "demo_keywords", "material contact", n_results=5, filters={"category": "contact"})
    assert [h.id for h in only] == ["cont"]
    exact = lookup(config, "demo", "demo_keywords", filters={"category": "section"})
    assert [h.id for h in exact] == ["sec"] and exact[0].distance is None
    text = format_hits(hits, 10, "demo/demo_keywords")
    assert "score" in text and "distance" in text and "source kw.jsonl chunk" in text and "more characters" in text


@needs_chroma
def test_build_refusals(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    with pytest.raises(KnowledgeError, match="already exists"):
        build_collection(config, "demo", "demo_keywords")
    assert build_collection(config, "demo", "demo_keywords", replace=True).count == 5
    # a database without MANIFEST.json was not built by Champion
    (kb.parent / "vector_db_extra").mkdir()
    (kb.parent / "vector_db_extra" / "chroma.sqlite3").write_bytes(b"old")
    with pytest.raises(KnowledgeError, match="not built by Champion"):
        build_collection(config, "demo", "demo_extra")


@needs_chroma
def test_duplicate_ids_refused(demo, tmp_path):
    config, _, _ = demo
    unregister_knowledge("demo")
    register_knowledge(KnowledgeSpec("demo", (KnowledgeCollection("demo_keywords", MODEL,
                                                                  documents=lambda kb: RECORDS + RECORDS[:1]),)))
    with pytest.raises(KnowledgeError, match="used twice"):
        build_collection(config, "demo", "demo_keywords")


@needs_chroma
def test_search_leaves_every_database_file_unchanged(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    store_mod.forget_store()
    db = kb.parent / "vector_db"
    before = fingerprint(db, True)
    search(config, "demo", "demo_keywords", "contact")
    lookup(config, "demo", "demo_keywords", ids=["cont"])
    assert fingerprint(db, True) == before


@needs_chroma
def test_changed_database_stops_the_store(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    store_mod.forget_store()
    search(config, "demo", "demo_keywords", "contact")
    db = kb.parent / "vector_db"
    (db / "extra.bin").write_bytes(b"someone wrote here")
    with pytest.raises(DatabaseChanged, match="changed while Champion had it open"):
        search(config, "demo", "demo_keywords", "contact")
    with pytest.raises(DatabaseChanged):  # stays stopped
        search(config, "demo", "demo_keywords", "contact")


@needs_chroma
def test_model_and_dimension_must_match_the_manifest(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    db = kb.parent / "vector_db"
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


def test_registry_rejects_bad_specs():
    with pytest.raises(ValueError, match="collection name"):
        register_knowledge(KnowledgeSpec("bad", (KnowledgeCollection("x", MODEL),)))
    with pytest.raises(ValueError, match="database must be"):
        register_knowledge(KnowledgeSpec("bad", (KnowledgeCollection("good_name", MODEL, database="../db"),)))
    with pytest.raises(ValueError, match="space"):
        register_knowledge(KnowledgeSpec("bad", (KnowledgeCollection("good_name", MODEL, space="manhattan"),)))


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


@needs_chroma
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


# ------------------------------------------------------------------ status and CLI

def test_status_without_data_warns(demo):
    config, _, _ = demo
    lines = {line.name: line for line in knowledge_status(config)}
    assert lines["Knowledge demo"].status == OK
    assert lines["Knowledge demo/vector_db"].status == WARN and "champion kb build" in lines["Knowledge demo/vector_db"].detail
    assert lines[f"Embedding {MODEL}"].status in (OK, WARN)
    assert knowledge_status(config, "nobody")[0].status == FAIL


@needs_chroma
def test_status_after_build_and_count_or_file_problems(demo):
    config, kb, _ = demo
    build_collection(config, "demo", "demo_keywords")
    lines = {line.name: line for line in knowledge_status(config, verify=True)}
    line = lines["Collection demo/demo_keywords"]
    assert line.status == OK and "5 entries" in line.detail and "1 source file(s) match" in line.detail
    (kb / "kw.jsonl").write_text("changed\n")
    store_mod.forget_store()
    line = {l.name: l for l in knowledge_status(config, verify=True)}["Collection demo/demo_keywords"]
    assert line.status == FAIL and "changed since the build" in line.detail
    db = kb.parent / "vector_db"
    data = json.loads((db / MANIFEST).read_text())
    data["collections"]["demo_keywords"]["count"] = 99
    (db / MANIFEST).write_text(json.dumps(data))
    line = {l.name: l for l in knowledge_status(config)}["Collection demo/demo_keywords"]
    assert line.status == FAIL and "MANIFEST.json says 99" in line.detail


@needs_chroma
def test_cli_kb_status_and_build(demo, capsys, monkeypatch):
    from champion import cli

    config, _, _ = demo
    monkeypatch.setattr(cli, "load_config", lambda path=None: config)
    assert cli.main(["kb", "build"]) == 2
    assert cli.main(["kb", "build", "--agent", "demo", "--collection", "demo_keywords"]) == 0
    assert "demo/demo_keywords: 5 entries" in capsys.readouterr().out
    assert cli.main(["kb", "build", "--agent", "demo", "--collection", "demo_keywords"]) == 1
    assert "already exists" in capsys.readouterr().err
    store_mod.forget_store()
    assert cli.main(["kb", "status", "--agent", "demo"]) == 0
    assert "Collection demo/demo_keywords" in capsys.readouterr().out


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
