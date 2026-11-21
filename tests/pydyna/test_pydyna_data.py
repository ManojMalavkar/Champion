"""Step T1.0: checks on the real PyDyna knowledge base.

The data is not in Git (docs/tracks/pydyna.md, Decisions). These tests run where
``knowledge/pydyna/knowledge-base/`` exists (the Linux worktree) or where
``CHAMPION_PYDYNA_KB`` points to a copy; elsewhere they are skipped.
Expected numbers come from the local PyDyna_Agent report of 21 Nov 2026.
"""

import os
from pathlib import Path

import pytest

from champion.specialists.pydyna import records as rec

REPO = Path(__file__).resolve().parents[2]
KB = Path(os.environ.get("CHAMPION_PYDYNA_KB") or REPO / "knowledge" / "pydyna" / "knowledge-base")


def _lines(name):
    with open(KB / name, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _need(*names):
    missing = [name for name in names if not (KB / name).is_file()]
    if missing:
        pytest.skip(f"PyDyna knowledge base not here ({KB}): missing {', '.join(missing)}")


SOURCE_PARTS = [f"pydyna_source-part-000{i}.jsonl" for i in range(1, 5)]


def test_pydyna_file_line_counts():
    _need(rec.KW_SCHEMA_FILE, rec.KW_REF_FILE, rec.API_DOCS_FILE, rec.DPF_POST_FILE, *SOURCE_PARTS)
    assert _lines(rec.KW_SCHEMA_FILE) == 3685
    assert _lines(rec.KW_REF_FILE) == 5
    assert _lines(rec.API_DOCS_FILE) == 5
    assert sum(_lines(name) for name in SOURCE_PARTS) == 62323
    assert _lines(rec.DPF_POST_FILE) == 1719


def test_pydyna_keywords_merge_to_3686():
    _need(rec.KW_SCHEMA_FILE, rec.KW_REF_FILE)
    keywords = rec.load_keywords(KB)
    assert len(keywords) == 3686
    both = [k for k, r in keywords.items() if r["sources"] == [rec.GENERATED_SOURCE, rec.CURATED_SOURCE]]
    curated_only = [k for k, r in keywords.items() if r["sources"] == [rec.CURATED_SOURCE]]
    assert sorted(both) == ["*CONTACT_AUTOMATIC_SURFACE_TO_SURFACE", "*CONTROL_TERMINATION",
                            "*MAT_024", "*SECTION_SHELL"]
    assert curated_only == ["*DATABASE_D3PLOT"]
    assert len(rec.keyword_documents(KB)) == 3686


def test_pydyna_mat_024_keeps_the_full_card():
    _need(rec.KW_SCHEMA_FILE, rec.KW_REF_FILE)
    mat = rec.load_keywords(KB)["*MAT_024"]
    assert len(mat["parameters"]) > 5  # the old agent returned only RO, E, PR, SIGY, ETAN
    assert {"RO", "E", "PR", "SIGY", "ETAN"} <= set(mat["parameters"])
    assert mat["parameters"]["RO"]["unit"] == "tonne/mm^3"
    assert "card" in mat["parameters"]["RO"]
    assert "*MAT_PIECEWISE_LINEAR_PLASTICITY" in mat["aliases"]
    assert rec.keyword_index(rec.load_keywords(KB))["*MAT_PIECEWISE_LINEAR_PLASTICITY"]["keyword"] == "*MAT_024"


def test_pydyna_api_has_62328_unique_ids():
    _need(rec.API_DOCS_FILE, *SOURCE_PARTS)
    docs = rec.api_documents(KB)  # raises on a duplicate id
    assert len(docs) == 62328
    assert sum(d.id.startswith("api:doc:") for d in docs) == 5


def test_pydyna_dpf_post_drops_repository_files_only():
    _need(rec.DPF_POST_FILE)
    docs = rec.dpf_post_documents(KB)
    assert 0 < len(docs) < 1719
    paths = {d.metadata["source_path"] for d in docs}
    assert ".pre-commit-config.yaml" not in paths and "CODE_OF_CONDUCT.md" not in paths
    assert all(rec.keep_dpf_post_path(p) for p in paths)
    assert any(p.endswith(".py") for p in paths)
