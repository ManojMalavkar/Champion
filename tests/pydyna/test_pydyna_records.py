"""Step T1.0: PyDyna knowledge records on small sample files (tests/pydyna/fixtures)."""

import json

import pytest

from champion.specialists.pydyna import records as rec

FIXTURES = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "knowledge-base"


def test_the_record_layer_needs_no_database_or_model():
    from pathlib import Path

    import champion.specialists.pydyna  # noqa: F401  (importable without chromadb)

    source = Path(rec.__file__).read_text(encoding="utf-8")
    assert "chromadb" not in source.split('"""', 2)[2]
    assert "sentence_transformers" not in source


def test_normalize_keyword():
    assert rec.normalize_keyword(" mat_024 ") == "*MAT_024"
    assert rec.normalize_keyword("*SECTION_SHELL") == "*SECTION_SHELL"


def test_curated_keyword_is_merged_not_replaced():
    keywords = rec.load_keywords(FIXTURES)
    mat = keywords["*MAT_024"]
    # all 9 generated fields stay (the old agent kept only the 3 curated ones here)
    assert set(mat["parameters"]) == {"MID", "RO", "E", "PR", "SIGY", "ETAN", "FAIL", "TDEL", "LCSS"}
    # curated values win key by key, generated keys stay
    assert mat["parameters"]["RO"] == {"type": "float", "required": True, "description": "Mass density",
                                       "card": 1, "unit": "tonne/mm^3"}
    assert mat["parameters"]["PR"]["max"] == 0.5
    assert mat["parameters"]["LCSS"]["default"] == 0
    assert mat["description"] == "Piecewise linear plasticity material model for metals."
    assert mat["schema_description"].startswith("LS-DYNA keyword *MAT_024")
    assert mat["aliases"] == ["*MAT_PIECEWISE_LINEAR_PLASTICITY"]
    assert mat["sources"] == [rec.GENERATED_SOURCE, rec.CURATED_SOURCE]


def test_curated_only_keyword_is_added_and_generated_only_keywords_stay():
    keywords = rec.load_keywords(FIXTURES)
    assert sorted(keywords) == ["*CONTROL_TERMINATION", "*DATABASE_D3PLOT", "*MAT_024", "*SECTION_SHELL"]
    assert keywords["*DATABASE_D3PLOT"]["sources"] == [rec.CURATED_SOURCE]
    assert keywords["*SECTION_SHELL"]["sources"] == [rec.GENERATED_SOURCE]
    assert "schema_description" not in keywords["*SECTION_SHELL"]


def test_merge_does_not_change_the_input_records():
    generated = {"keyword": "*K", "description": "g", "parameters": {"A": {"type": "int", "card": 1}}}
    curated = {"keyword": "*K", "description": "c", "parameters": {"A": {"required": True}}}
    before = json.dumps([generated, curated], sort_keys=True)
    merged = rec.merge_keyword(generated, curated)
    assert json.dumps([generated, curated], sort_keys=True) == before
    assert merged["parameters"]["A"] == {"type": "int", "card": 1, "required": True}


def test_alias_lookup_never_hides_a_real_keyword():
    records = {"*A": {"keyword": "*A", "aliases": ["*B"]}, "*B": {"keyword": "*B", "aliases": []}}
    index = rec.keyword_index(records)
    assert index["*B"]["keyword"] == "*B"
    index = rec.keyword_index(rec.load_keywords(FIXTURES))
    assert index["*MAT_PIECEWISE_LINEAR_PLASTICITY"]["keyword"] == "*MAT_024"


def test_keyword_documents_have_stable_ids_and_old_text_layout():
    docs = {d.id: d for d in rec.keyword_documents(FIXTURES)}
    assert set(docs) == {"kw:*CONTROL_TERMINATION", "kw:*DATABASE_D3PLOT", "kw:*MAT_024", "kw:*SECTION_SHELL"}
    shell = docs["kw:*SECTION_SHELL"]
    assert shell.text == "*SECTION_SHELL section schema shell"  # as sacreate.py: keyword category description
    assert shell.metadata == {"keyword": "*SECTION_SHELL", "category": "section", "sources": rec.GENERATED_SOURCE}
    mat = docs["kw:*MAT_024"].text
    assert mat.startswith("*MAT_024 material Piecewise linear plasticity") and "codegen schema" in mat


def test_ids_do_not_depend_on_input_order(tmp_path):
    for name in ("keyword_schema.jsonl", "keyword_reference.jsonl", "pydyna_docs.jsonl",
                 "pydyna_source-part-0001.jsonl", "pydyna_source-part-0002.jsonl", "pydpf_post_source.jsonl"):
        lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        (tmp_path / name).write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    for build in (rec.keyword_documents, rec.api_documents, rec.dpf_post_documents):
        assert {d.id: d for d in build(FIXTURES)} == {d.id: d for d in build(tmp_path)}


def test_api_documents_match_sacreate_layout():
    docs = rec.api_documents(FIXTURES)
    assert [d.id for d in docs] == [
        "api:doc:DynaBase.__init__", "api:doc:DynaSolution.__init__",
        "api:src/ansys/dyna/core/deck.py#0", "api:src/ansys/dyna/core/deck.py#1", "api:examples/shell.py#0",
    ]
    assert docs[0].text == "DynaBase.__init__\nBase class.\nSignature: DynaBase()\nExample: model = DynaBase()"
    assert docs[0].metadata == {"class": "DynaBase", "method": "__init__", "source_path": "", "chunk_index": -1}
    assert docs[2].text == ("PyDyna source.src/ansys/dyna/core/deck.py#chunk-1\n"
                            "Source: src/ansys/dyna/core/deck.py\nclass Deck: ...")
    assert docs[2].metadata["chunk_index"] == 0


def test_single_source_file_is_used_when_there_are_no_parts(tmp_path):
    (tmp_path / "pydyna_source.jsonl").write_text(
        (FIXTURES / "pydyna_source-part-0002.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert [d.id for d in rec.api_documents(tmp_path)] == ["api:examples/shell.py#0"]


@pytest.mark.parametrize("path, keep", [
    (".pre-commit-config.yaml", False), ("CODE_OF_CONDUCT.md", False), ("CONTRIBUTING.md", False),
    (".github/workflows/ci.md", False), ("pyproject.toml", False), ("", False),
    ("README.md", True), ("src/ansys/dpf/post/simulation.py", True),
    ("examples/01-basics/01-energy.py", True), ("doc/source/index.rst", True),
])
def test_dpf_post_filter(path, keep):
    assert rec.keep_dpf_post_path(path) is keep


def test_dpf_post_documents_leave_out_repository_files():
    docs = rec.dpf_post_documents(FIXTURES)
    assert [d.metadata["source_path"] for d in docs] == [
        "README.md", "src/ansys/dpf/post/simulation.py", "examples/01-basics/01-energy.py", "doc/source/index.rst"]
    assert docs[1].id == "dpfpost:src/ansys/dpf/post/simulation.py#0"
    assert docs[1].text == ("PyDPF-Post source.src/ansys/dpf/post/simulation.py#chunk-1\n"
                            "Source: src/ansys/dpf/post/simulation.py\nclass Simulation:")
    assert docs[1].metadata["source_project"] == "PyDPF-Post"


def test_duplicate_ids_are_an_error():
    doc = rec.Document(id="api:x#0", text="t")
    with pytest.raises(ValueError, match="duplicate record id api:x#0"):
        rec.unique_ids([doc, doc])


def test_bad_json_line_names_file_and_line(tmp_path):
    (tmp_path / "pydyna_docs.jsonl").write_text('{"class": "A"}\n\n{bad\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"pydyna_docs.jsonl line 3"):
        rec.api_documents(tmp_path)


def test_missing_files_give_empty_collections(tmp_path):
    assert rec.collection_documents(tmp_path) == {rec.KW_COLLECTION: [], rec.API_COLLECTION: [],
                                                  rec.DPF_POST_COLLECTION: []}


def test_check_params_types_required_and_limits():
    mat = rec.load_keywords(FIXTURES)["*MAT_024"]
    ok = rec.check_params(mat, {"RO": 7.85e-9, "PR": 0.3, "SIGY": 250, "LCSS": 3})
    assert ok == {"valid": True, "keyword": "*MAT_024", "errors": [], "warnings": []}
    bad = rec.check_params(mat, {"RO": "7.85e-9", "PR": 0.7, "LCSS": 2.5, "TDEL": True, "XYZ": 1})
    assert bad["valid"] is False
    assert bad["errors"] == ["Missing required parameter: SIGY", "RO='7.85e-9' is not of type float",
                             "LCSS=2.5 is not of type int", "TDEL=True is not of type float"]
    assert bad["warnings"] == ["PR=0.7 is above maximum (0.5)", "Unknown parameter: XYZ"]
    shell = rec.load_keywords(FIXTURES)["*SECTION_SHELL"]
    assert rec.check_params(shell, {"TITLE": 5})["errors"] == ["TITLE=5 is not of type string"]
