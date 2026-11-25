"""PyDyna tools (track T1): the old PyDyna_Agent tools on Champion's guard.

Searches use a fake backend (no model, no database); one test builds a small
real Chroma database with a fake embedding (skipped without chromadb).
"""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from agents import RunContextWrapper

from champion.core.config import load_config
from champion.core.context import ChampionContext
from champion.policy.tool_policy import rule_for
from champion.specialists.base import walk_specs
from champion.specialists.pydyna import PYDYNA
from champion.specialists.pydyna import tools as pt
from champion.specialists.registry import check_spec
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError, run_guarded

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "knowledge-base"
TOOL_NAMES = ["lookup_keyword", "validate_keyword_params", "search_keyword", "search_pydyna_api", "search_pydpf_post",
              "lookup_api", "check_pydyna_script", "lookup_dpf_api", "find_dpf_operators", "check_dpf_script",
              "search_pydpf_core"]


@pytest.fixture
def project(tmp_path):
    kb = tmp_path / "knowledge" / "pydyna" / "knowledge-base"
    shutil.copytree(FIXTURES, kb)
    (tmp_path / "config").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        f"llm:\n  model_id: m\nexecution:\n  working_directory: '{tmp_path / 'work'}'\n")
    config = load_config(tmp_path / "config" / "config.yaml", env={}, cwd=tmp_path)
    return config, kb


def fake_m1(monkeypatch, hits):
    """M1 search with a fake model and store; ``hits``: collection -> [(id, text, metadata, distance)]."""
    import importlib

    from champion.knowledge.store import Hit as SharedHit
    shared = importlib.import_module("champion.knowledge.search")
    calls = []

    class Embedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    class Store:
        def query(self, name, vector, count, filters):
            calls.append((name, count, filters))
            return [SharedHit(*row) for row in hits.get(name, [])][:count]

    monkeypatch.setattr(shared, "embedder_for", lambda *args: Embedder())
    monkeypatch.setattr(shared, "store_for", lambda path, verify: Store())
    return calls


def call(config, tool, **args):
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    return asyncio.run(tool.on_invoke_tool(RunContextWrapper(context=context), json.dumps(args)))


def test_pydyna_group_has_four_sub_agents_and_every_tool_a_policy_entry():
    assert PYDYNA.tool_name == "pydyna_agent" and check_spec(PYDYNA) == []
    assert [m.name for m in PYDYNA.members] == ["pydyna_keyword", "pydyna_code", "pydyna_dpf_post",
                                                "pydyna_dpf_core"]
    assert [m.title for m in PYDYNA.members] == ["Keyword", "Code", "DPF-Post", "DPF-Core"]
    assert PYDYNA.members[0].tool_names == ["lookup_keyword", "search_keyword", "validate_keyword_params",
                                            "lookup_api"]
    assert PYDYNA.members[1].tool_names == ["lookup_api", "search_pydyna_api", "lookup_keyword",
                                            "check_pydyna_script"]
    assert PYDYNA.members[2].tool_names == ["search_pydpf_post", "lookup_dpf_api", "find_dpf_operators",
                                            "check_dpf_script"]
    assert PYDYNA.members[3].tool_names == ["search_pydpf_core", "lookup_dpf_api", "find_dpf_operators",
                                            "check_dpf_script"]
    used = {name for spec in walk_specs(PYDYNA) for name in spec.tool_names}
    assert used == set(TOOL_NAMES)
    for name in TOOL_NAMES:
        rule = rule_for(name)
        assert rule.right == "none" and rule.approval == "never" and not rule.sends_data_out
    assert "get_material_model" not in used and "get_contact_type" not in used  # user, 21 Nov 2026


def test_default_paths_are_in_the_project_and_config_paths_win(project, tmp_path):
    config, kb = project
    assert pt.database_path(config) == tmp_path / "knowledge" / "pydyna" / "vector_db"
    assert pt.knowledge_base_path(config) == kb
    config.data["pydyna"]["keyword_reference_path"] = str(tmp_path / "other" / "keyword_reference.jsonl")
    config.data["pydyna"]["database_path"] = str(tmp_path / "db")
    assert pt.knowledge_base_path(config) == tmp_path / "other"
    assert pt.database_path(config) == tmp_path / "db"


def test_lookup_keyword_returns_the_merged_record_with_sources(project):
    config, _ = project
    mat = json.loads(pt.lookup(config, "mat_024"))
    assert mat["keyword"] == "*MAT_024" and len(mat["parameters"]) == 9
    assert mat["parameters"]["RO"]["unit"] == "tonne/mm^3" and mat["parameters"]["RO"]["card"] == 1
    assert mat["sources"] == ["PyDyna codegen schema", "curated reference"]
    alias = json.loads(pt.lookup(config, "*MAT_PIECEWISE_LINEAR_PLASTICITY"))
    assert alias["keyword"] == "*MAT_024" and alias["found_as_alias"] == "*MAT_PIECEWISE_LINEAR_PLASTICITY"


def test_lookup_of_an_unknown_keyword_names_close_ones(project):
    config, _ = project
    with pytest.raises(ToolError, match=r"\*MAT_02 is not in .* Close names: \*MAT_024"):
        pt.lookup(config, "MAT_02")
    with pytest.raises(ToolError, match="Use search_keyword"):
        pt.lookup(config, "*NOTHING_LIKE_IT")


def test_missing_knowledge_base_is_a_clear_error(project, tmp_path):
    config, _ = project
    config.data["pydyna"]["keyword_reference_path"] = str(tmp_path / "empty" / "keyword_reference.jsonl")
    with pytest.raises(ToolError, match="no keyword files in .*pydyna.keyword_reference_path"):
        pt.lookup(config, "*MAT_024")


def test_keywords_are_read_again_when_a_file_changes(project):
    config, kb = project
    assert "*NEW_ONE" not in pt.keywords(config)[0]
    with open(kb / "keyword_reference.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"keyword": "*NEW_ONE", "category": "control", "description": "n",
                                 "parameters": {}}) + "\n")
    assert "*NEW_ONE" in pt.keywords(config)[0]


def test_validate_keyword_params_checks_types_and_json(project):
    config, _ = project
    result = json.loads(pt.validate(config, "*MAT_024", '{"RO": "7.85e-9", "PR": 0.7, "SIGY": 250}'))
    assert result["valid"] is False
    assert result["errors"] == ["RO='7.85e-9' is not of type float"]
    assert result["warnings"] == ["PR=0.7 is above maximum (0.5)"]
    with pytest.raises(ToolError, match="JSON object"):
        pt.validate(config, "*MAT_024", "RO=7.85e-9")
    with pytest.raises(ToolError, match="JSON object"):
        pt.validate(config, "*MAT_024", "[1, 2]")
    with pytest.raises(ToolError, match="not in the PyDyna knowledge base"):
        pt.validate(config, "*NOPE", "{}")


def test_search_keyword_category_is_normalised_and_an_unknown_one_names_the_known_ones(project, monkeypatch):
    config, _ = project
    calls = fake_m1(monkeypatch, {"lsdyna_keywords": [
        ("kw:*MAT_024", "*MAT_024 material plasticity", {"keyword": "*MAT_024", "category": "material"}, 0.25)]})
    text = call(config, pt.search_keyword, query="steel plasticity", n_results=8, category=" Material ")
    assert calls == [("lsdyna_keywords", 8, {"category": "material"})]
    assert "*MAT_024" in text and "score 0.75" in text
    error = call(config, pt.search_keyword, query="x", n_results=8, category="materials")
    assert error.startswith("ERROR: unknown category materials; known: control, material, output, section")
    assert len(calls) == 1  # refused before the search
    call(config, pt.search_keyword, query="anything", n_results=2, category=None)
    assert calls[-1][0] == "lsdyna_keywords" and not calls[-1][2]  # no category: no filter


def test_search_pydyna_api_drops_tests_and_codegen_of_the_old_database(project, monkeypatch):
    config, _ = project
    fake_m1(monkeypatch, {"pydyna_api": [
        ("a0", "test deck", {"source_path": "tests/test_deck.py", "chunk_index": 0}, 0.1),
        ("a1", "manifest", {"source_path": "codegen/manifest.json", "chunk_index": 0}, 0.2),
        ("a2", "docs", {"class": "DynaBase", "method": "__init__", "source_path": "", "chunk_index": -1}, 0.3),
        ("a3", "deck", {"source_path": "src/ansys/dyna/core/deck.py", "chunk_index": 1}, 0.4),
        ("a4", "example", {"source_path": "examples/shell.py", "chunk_index": 0}, 0.5)]})
    text = call(config, pt.search_pydyna_api, query="deck", n_results=2)
    assert "2 result(s)" in text and "id a0" not in text and "id a1" not in text and "id a4" not in text
    assert text.index("id a2") < text.index("id a3")  # docs records without a file are kept
    assert "source src/ansys/dyna/core/deck.py chunk 1" in text


def test_search_pydpf_post_drops_repository_files_of_the_old_database(project, monkeypatch):
    config, _ = project
    fake_m1(monkeypatch, {"pydpf_post": [
        ("d0", "pre-commit", {"source_path": ".pre-commit-config.yaml", "chunk_index": 0}, 0.1),
        ("d1", "conduct", {"source_path": "CODE_OF_CONDUCT.md", "chunk_index": 0}, 0.2),
        ("d1b", "test", {"source_path": "tests/test_simulation.py", "chunk_index": 0}, 0.25),
        ("d2", "energy", {"source_path": "examples/01-energy.py", "chunk_index": 0}, 0.3),
        ("d3", "simulation", {"source_path": "src/ansys/dpf/post/simulation.py", "chunk_index": 2}, 0.4),
        ("d4", "more", {"source_path": "doc/source/index.rst", "chunk_index": 0}, 0.5)]})
    text = call(config, pt.search_pydpf_post, query="energy", n_results=2)
    assert "2 result(s)" in text and text.index("id d2") < text.index("id d3")
    assert all(f"id {name}\n" not in text and f"id {name} " not in text for name in ("d0", "d1", "d1b", "d4"))


def test_search_without_database_says_how_to_build(project):
    pytest.importorskip("chromadb")
    pytest.importorskip("sentence_transformers")  # without them M1 names the missing packages first
    config, _ = project
    text = call(config, pt.search_pydpf_core, query="energy", n_results=1)
    assert text.startswith("ERROR:") and "no database at" in text and "vector_db_dpf_core" in text
    assert "champion kb build" in text


def test_tools_run_through_the_guard_even_without_read_access(project, monkeypatch):
    config, _ = project
    fake_m1(monkeypatch, {"lsdyna_keywords": [
        ("kw:*MAT_024", "*MAT_024 material", {"keyword": "*MAT_024", "category": "material"}, 0.25)]})
    assert "*MAT_024" in call(config, pt.search_keyword, query="steel", n_results=1, category="material")
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    error = asyncio.run(run_guarded(RunContextWrapper(context=context), "lookup_keyword", {"keyword_name": "*NOPE"},
                                    lambda: pt.lookup(config, "*NOPE")))
    assert error.startswith("ERROR: keyword *NOPE is not in the PyDyna knowledge base")


def test_real_chroma_database_with_category_filter_through_m1(project, monkeypatch):
    """A real Chroma database without MANIFEST.json (like the old agent's), searched read-only by M1."""
    chromadb = pytest.importorskip("chromadb")
    import importlib

    from chromadb.config import Settings
    shared = importlib.import_module("champion.knowledge.search")

    def letters(text):  # fake embedding: counts of a few letters (enough to rank, no model)
        return [float(text.lower().count(c)) + 0.01 for c in "aeioumtsl"]

    class Embedder:
        def embed(self, texts):
            return [letters(t) for t in texts]

    config, _ = project
    db = pt.database_path(config)
    documents = ["*MAT_024 material metal plasticity", "*MAT_020 material rigid", "*SECTION_SHELL section shell thickness"]
    client = chromadb.PersistentClient(path=str(db), settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection("lsdyna_keywords", embedding_function=None)
    collection.add(ids=["kw:*MAT_024", "kw:*MAT_020", "kw:*SECTION_SHELL"], documents=documents,
                   embeddings=[letters(d) for d in documents],
                   metadatas=[{"keyword": "*MAT_024", "category": "material"},
                              {"keyword": "*MAT_020", "category": "material"},
                              {"keyword": "*SECTION_SHELL", "category": "section"}])
    del client
    monkeypatch.setattr(shared, "embedder_for", lambda *args: Embedder())
    filtered = call(config, pt.search_keyword, query="shell thickness", n_results=3, category="material")
    assert "2 result(s)" in filtered and "SECTION_SHELL" not in filtered and "distance" in filtered
    unfiltered = call(config, pt.search_keyword, query="shell thickness", n_results=3, category=None)
    assert "3 result(s)" in unfiltered and "*SECTION_SHELL" in unfiltered


# T1.1: test the actual M1 tools, not only the old work helpers.
def test_m1_registers_four_existing_collections_and_core_builder(project):
    from champion.knowledge.registry import knowledge_spec
    config, kb = project
    spec = knowledge_spec("pydyna")
    assert [c.name for c in spec.collections] == ["lsdyna_keywords", "pydyna_api", "pydpf_post", "pydpf_core"]
    assert [c.database for c in spec.collections] == ["vector_db"] * 3 + ["vector_db_dpf_core"]
    assert all(c.model == "BAAI/bge-small-en-v1.5" and c.space == "l2" for c in spec.collections)
    assert all(c.documents is None for c in spec.collections[:3])
    assert spec.collection("pydpf_core").documents is pt._core_documents
    assert config.data["pydyna"] == dict(PYDYNA.config_defaults)


def test_m1_config_root_moves_all_paths_and_matches_example(project, tmp_path):
    from champion.core.config import example_fragment_problems
    config, _ = project
    config.data["pydyna"]["knowledge_root"] = str(tmp_path / "other_knowledge")
    assert pt.database_path(config) == tmp_path / "other_knowledge" / "vector_db"
    assert pt.dpf_core_database_path(config) == tmp_path / "other_knowledge" / "vector_db_dpf_core"
    assert pt.knowledge_base_path(config) == tmp_path / "other_knowledge" / "knowledge-base"
    assert example_fragment_problems() == []


@pytest.mark.parametrize("tool_name,collection,database", [
    ("search_keyword", "lsdyna_keywords", "vector_db"),
    ("search_pydyna_api", "pydyna_api", "vector_db"),
    ("search_pydpf_post", "pydpf_post", "vector_db"),
    ("search_pydpf_core", "pydpf_core", "vector_db_dpf_core"),
])
def test_live_search_tools_use_m1_filters_sources_and_guard(project, monkeypatch, tool_name, collection, database):
    import importlib
    from champion.knowledge.store import Hit as SharedHit
    shared = importlib.import_module("champion.knowledge.search")
    config, _ = project
    calls = []
    class Embedder:
        def embed(self, texts):
            assert texts == ["energy"]
            return [[1.0, 0.0]]
    class Store:
        def query(self, name, vector, count, filters):
            calls.append((name, count, filters))
            if name == "lsdyna_keywords":
                return [SharedHit("kw:*MAT_024", "*MAT_024 material", {"category": "material"}, 0.25)]
            return [SharedHit("bad", "test", {"source_path": "tests/a.py"}, 0.1),
                    SharedHit("good", "energy example", {"source_path": "examples/energy.py", "chunk_index": 0}, 0.2)]
    def store(path, verify):
        assert path == pt.database_path(config).parent / database
        assert verify is True
        return Store()
    monkeypatch.setattr(shared, "embedder_for", lambda *args: Embedder())
    monkeypatch.setattr(shared, "store_for", store)
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    tool = getattr(pt, tool_name)
    args = {"query": "energy", "n_results": 1}
    if collection == "lsdyna_keywords":
        args["category"] = "Material"
    output = asyncio.run(tool.on_invoke_tool(RunContextWrapper(context=context), json.dumps(args)))
    assert f"pydyna/{collection}: 1 result(s)" in output and "distance" in output
    if collection == "lsdyna_keywords":
        assert calls == [(collection, 1, {"category": "material"})]
        assert "*MAT_024" in output
    else:
        assert calls == [(collection, 5, {})]
        assert "source examples/energy.py chunk 0" in output and "id bad" not in output


def test_m1_live_tool_rejects_outside_root_and_legacy_mismatch(project, tmp_path):
    config, _ = project
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    wrapper = RunContextWrapper(context=context)
    config.data["pydyna"]["knowledge_root"] = str(tmp_path.parent / "outside")
    result = asyncio.run(pt.search_pydpf_core.on_invoke_tool(wrapper, '{"query":"x","n_results":1}'))
    assert result.startswith("ERROR:") and "outside the project" in result
    config.data["pydyna"]["knowledge_root"] = ""
    config.data["pydyna"]["database_path"] = str(tmp_path / "old_database")
    result = asyncio.run(pt.search_pydyna_api.on_invoke_tool(wrapper, '{"query":"x","n_results":1}'))
    assert result.startswith("ERROR:") and "clear the old path keys" in result

