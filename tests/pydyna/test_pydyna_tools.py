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
from champion.specialists.pydyna.search import ChromaSearch, Hit, set_backend
from champion.specialists.registry import check_spec
from champion.storage.audit import AuditLog
from champion.tools.guard import ToolError, run_guarded

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "knowledge-base"
TOOL_NAMES = ["lookup_keyword", "validate_keyword_params", "search_keyword", "search_pydyna_api", "search_pydpf_post",
              "lookup_api", "check_pydyna_script", "lookup_dpf_api", "find_dpf_operators", "check_dpf_script",
              "search_pydpf_core"]


class FakeBackend:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def query(self, collection, text, n_results, where=None):
        self.calls.append((collection, text, n_results, where))
        rows = [h for h in self.hits.get(collection, [])
                if not where or all(h.metadata.get(k) == v for k, v in where.items())]
        return rows[:n_results]


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


def hit(collection_id, document, distance, **metadata):
    return Hit(id=collection_id, document=document, metadata=metadata, distance=distance)


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


def test_search_keyword_category_filter_returns_only_that_family(project):
    config, _ = project
    backend = FakeBackend({"lsdyna_keywords": [
        hit("kw_1", "*SECTION_SHELL section shell", 0.20, keyword="*SECTION_SHELL", category="section"),
        hit("kw_2", "*MAT_024 material plasticity", 0.25, keyword="*MAT_024", category="material"),
        hit("kw_3", "*CONTROL_TERMINATION control", 0.30, keyword="*CONTROL_TERMINATION", category="control"),
    ]})
    rows = json.loads(pt.search_keywords(config, backend, "steel plasticity", category="Material"))
    assert backend.calls[-1] == ("lsdyna_keywords", "steel plasticity", 8, {"category": "material"})
    assert [r["keyword"] for r in rows] == ["*MAT_024"] and {r["category"] for r in rows} == {"material"}
    assert rows[0]["distance"] == 0.25 and rows[0]["score"] == 0.75
    everything = json.loads(pt.search_keywords(config, backend, "anything", n_results=2))
    assert backend.calls[-1][3] is None and len(everything) == 2


def test_search_keyword_rejects_unknown_category_and_bad_counts(project):
    config, _ = project
    backend = FakeBackend({})
    with pytest.raises(ToolError, match="unknown category materials; known: control, material, output, section"):
        pt.search_keywords(config, backend, "x", category="materials")
    for bad in (0, 21, True):
        with pytest.raises(ToolError, match="1 to 20"):
            pt.search_keywords(config, backend, "x", n_results=bad)
    with pytest.raises(ToolError, match="query is empty"):
        pt.search_keywords(config, backend, "  ")
    assert backend.calls == []


def test_search_pydyna_api_shows_source_and_chunk(project):
    config, _ = project
    backend = FakeBackend({"pydyna_api": [
        hit("api_9", "PyDyna source.deck.py#chunk-1\nSource: src/deck.py\n" + "x" * 3000, 0.4,
            **{"class": "PyDyna source", "method": "deck.py#chunk-1", "source_path": "src/deck.py",
               "chunk_index": 0})]})
    rows = json.loads(pt.search_api(config, backend, "deck", n_results=3))
    assert backend.calls == [("pydyna_api", "deck", 15, None)]  # asks for 5 x n, keeps n
    assert rows[0]["source_path"] == "src/deck.py" and rows[0]["chunk_index"] == 0
    assert len(rows[0]["text"]) == pt.CODE_TEXT_CHARS


def test_search_pydyna_api_drops_tests_and_codegen_of_the_old_database(project):
    config, _ = project
    backend = FakeBackend({"pydyna_api": [
        hit("a0", "test deck", 0.1, source_path="tests/test_deck.py", chunk_index=0),
        hit("a1", "manifest", 0.2, source_path="codegen/manifest.json", chunk_index=0),
        hit("a2", "docs", 0.3, **{"class": "DynaBase", "method": "__init__", "source_path": "", "chunk_index": -1}),
        hit("a3", "deck", 0.4, source_path="src/ansys/dyna/core/deck.py", chunk_index=1),
        hit("a4", "example", 0.5, source_path="examples/shell.py", chunk_index=0),
    ]})
    rows = json.loads(pt.search_api(config, backend, "deck", n_results=2))
    assert [(r["method"], r["source_path"]) for r in rows] == [("__init__", ""), ("", "src/ansys/dyna/core/deck.py")]


def test_search_pydpf_post_drops_repository_files_of_the_old_database(project):
    config, _ = project
    backend = FakeBackend({"pydpf_post": [
        hit("d0", "pre-commit", 0.1, source_path=".pre-commit-config.yaml", chunk_index=0),
        hit("d1", "conduct", 0.2, source_path="CODE_OF_CONDUCT.md", chunk_index=0),
        hit("d1b", "test", 0.25, source_path="tests/test_simulation.py", chunk_index=0),
        hit("d2", "energy", 0.3, source_path="examples/01-energy.py", chunk_index=0),
        hit("d3", "simulation", 0.4, source_path="src/ansys/dpf/post/simulation.py", chunk_index=2),
        hit("d4", "more", 0.5, source_path="doc/source/index.rst", chunk_index=0),
    ]})
    rows = json.loads(pt.search_dpf_post(config, backend, "energy", n_results=2))
    assert backend.calls == [("pydpf_post", "energy", 10, None)]  # asks for 5 x n, keeps n
    assert [r["source_path"] for r in rows] == ["examples/01-energy.py", "src/ansys/dpf/post/simulation.py"]


def test_search_without_database_says_what_to_set(project):
    config, _ = project
    backend = ChromaSearch(pt.database_path(config))
    with pytest.raises(ToolError, match="no PyDyna database at .*pydyna.database_path"):
        pt.search_api(config, backend, "deck")


def test_tools_run_through_the_guard_and_are_audited_even_without_read_access(project):
    config, _ = project
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog())
    set_backend(pt.database_path(config), FakeBackend({"lsdyna_keywords": [
        hit("kw_2", "*MAT_024 material", 0.25, keyword="*MAT_024", category="material")]}))
    try:
        wrapper = RunContextWrapper(context=context)
        text = asyncio.run(run_guarded(wrapper, "search_keyword", {"query": "steel"},
                                       lambda: pt.search_keywords(config, pt._backend(context), "steel",
                                                                  "material")))
        assert json.loads(text)[0]["keyword"] == "*MAT_024"
        error = asyncio.run(run_guarded(wrapper, "lookup_keyword", {"keyword_name": "*NOPE"},
                                        lambda: pt.lookup(config, "*NOPE")))
        assert error.startswith("ERROR: keyword *NOPE is not in the PyDyna knowledge base")
    finally:
        set_backend(pt.database_path(config), None)


def test_real_chroma_database_with_category_filter(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    from chromadb.api.types import EmbeddingFunction
    from chromadb.config import Settings

    class Letters(EmbeddingFunction):
        """Fake embedding: counts of a few letters (enough to rank, no model)."""

        def __init__(self):
            pass

        def __call__(self, input):
            return [[float(text.lower().count(c)) for c in "aeioumtsl"] for text in input]

        @staticmethod
        def name():
            return "letters-test"

        def get_config(self):
            return {}

        @staticmethod
        def build_from_config(config):
            return Letters()

    db = tmp_path / "vector_db"
    client = chromadb.PersistentClient(path=str(db), settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection("lsdyna_keywords", embedding_function=Letters())
    collection.add(ids=["kw:*MAT_024", "kw:*MAT_020", "kw:*SECTION_SHELL"],
                   documents=["*MAT_024 material metal plasticity", "*MAT_020 material rigid",
                              "*SECTION_SHELL section shell thickness"],
                   metadatas=[{"keyword": "*MAT_024", "category": "material"},
                              {"keyword": "*MAT_020", "category": "material"},
                              {"keyword": "*SECTION_SHELL", "category": "section"}])
    del client
    search = ChromaSearch(db, embedding=Letters())
    hits = search.query("lsdyna_keywords", "shell thickness", 3, {"category": "material"})
    assert {h.metadata["category"] for h in hits} == {"material"} and len(hits) == 2
    assert all(isinstance(h.distance, float) for h in hits)
    unfiltered = search.query("lsdyna_keywords", "shell thickness", 3)
    assert {h.metadata["category"] for h in unfiltered} == {"material", "section"}
