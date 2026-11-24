"""T1.3b: knowledge source generator (source_data.py), index builder (build_index.py) and the
DPF-Core search (search_pydpf_core). Runs on a small fake repository in tmp_path; the clone
test uses a local Git repository (skipped without git); nothing goes to the network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest

from champion.core.config import load_config
from champion.specialists.pydyna import build_index as bi
from champion.specialists.pydyna import records as rec
from champion.specialists.pydyna import source_data as sd
from champion.specialists.pydyna import tools as pt
from champion.specialists.pydyna.search import Hit

CORE = sd.PROJECTS["pydpf-core"]

PY = '''"""Module doc."""

import os


class Model:
    """A result file."""

    @property
    def results(self):
        return 1

    def plot(self):
        return 2


def helper():
    return 3
'''

REPO = {
    "src/ansys/dpf/core/model.py": PY,
    "doc/source/index.rst": "Title\n=====\n\nIntro text.\n\nSection\n-------\n\nMore text.\n",
    "examples/01-basic/readme.md": "# Basics\n\n```python\n# not a heading\nx = 1\n```\n\n## Next\n\nText.\n",
    "tests/test_model.py": "def test_x():\n    pass\n",
    ".github/workflows/ci.md": "# ci\n",
    "CODE_OF_CONDUCT.md": "# Code of conduct\n",
    "src/ansys/dpf/core/empty.py": "\n\n",
    "src/ansys/dpf/core/data.json": "{}",
}


def make_repo(root):
    for name, text in REPO.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / "src/ansys/dpf/core/latin.py").write_bytes(b"# \xe9\n")
    (root / "src/ansys/dpf/core/big.py").write_text("x = 1\n" * (sd.MAX_FILE_BYTES // 6 + 10))
    return root


# ---------------------------------------------------------------- chunks

def test_python_chunks_cut_at_class_and_def_and_keep_decorators():
    chunks = sd.chunk_text(PY, ".py", limit=60)
    texts = [t for _, _, t in chunks]
    assert all(len(t) <= 60 for t in texts)
    assert any(t.startswith("    @property\n    def results") for t in texts)  # decorator stays with its def
    assert any(t.startswith("def helper") for t in texts)
    assert "\n".join(texts).count("class Model") == 1
    assert chunks[0][0] == 1 and chunks[-1][1] == len(PY.splitlines())
    whole = sd.chunk_text(PY, ".py")  # under the default limit: blocks are packed into one chunk
    assert len(whole) == 1 and whole[0][2] == PY.strip("\n")


def test_long_blocks_and_lines_are_cut_without_losing_text():
    text = "def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(400)) + "\n" + "y = '" + "a" * 5000 + "'\n"
    chunks = sd.chunk_text(text, ".py")
    assert all(len(t) <= sd.CHUNK_CHARS for _, _, t in chunks) and len(chunks) > 3
    assert "".join(t.replace("\n", "") for _, _, t in chunks) == text.replace("\n", "")


def test_markdown_and_rst_headings_and_code_blocks():
    md = sd.chunk_text(REPO["examples/01-basic/readme.md"], ".md", limit=60)
    assert [t.splitlines()[0] for _, _, t in md] == ["# Basics", "## Next"]
    rst = sd.chunk_text(REPO["doc/source/index.rst"], ".rst", limit=30)
    assert [t.splitlines()[0] for _, _, t in rst] == ["Title", "Section"]
    assert sd.chunk_text("\n \n", ".py") == [] and sd.chunk_text("", ".md") == []


# ---------------------------------------------------------------- files of a repository

def test_collect_keeps_source_docs_examples_in_the_old_record_format(tmp_path):
    records, stats = sd.collect(CORE, make_repo(tmp_path / "repo"), "0.16.3", "abc123")
    paths = sorted({r["source_path"] for r in records})
    assert paths == ["doc/source/index.rst", "examples/01-basic/readme.md", "src/ansys/dpf/core/model.py"]
    assert stats["files_kept"] == 3 and stats["files_seen"] == 9  # .github is hidden: not walked
    assert stats["skipped"] == {"not source, docs or examples": 3, "empty": 1, "not UTF-8": 1,
                                "larger than 2 MB": 1}
    first = records[0]
    assert set(first) == {"class", "method", "description", "source_project", "source_path", "chunk_index",
                          "start_line", "end_line", "version", "commit"}
    assert first["class"] == "PyDPF-Core source" and first["source_project"] == "PyDPF-Core"
    assert first["method"] == f"{first['source_path']}#chunk-1" and first["chunk_index"] == 0
    excluded, _ = sd.collect(CORE, tmp_path / "repo", "0.16.3", "abc123", excludes=("examples/*",))
    assert {r["source_path"] for r in excluded} == {"doc/source/index.rst", "src/ansys/dpf/core/model.py"}


def test_generate_writes_files_and_manifest_that_records_py_reads(tmp_path):
    out = tmp_path / "kb"
    manifest = sd.generate(CORE, out, ref="v0.16.3", source=make_repo(tmp_path / "repo"))
    assert manifest["version"] == "0.16.3" and manifest["records"] > 0
    data = (out / "pydpf_core_source.jsonl").read_bytes()
    assert manifest["files"] == [{"name": "pydpf_core_source.jsonl", "records": manifest["records"],
                                  "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    assert json.loads((out / "MANIFEST-pydpf-core.json").read_text())["records"] == manifest["records"]
    documents = rec.dpf_core_documents(out)
    assert len(documents) == manifest["records"]
    assert documents[0].id.startswith("dpfcore:") and documents[0].metadata["version"] == "0.16.3"
    assert documents[0].metadata["source_project"] == "PyDPF-Core"
    with pytest.raises(sd.SourceDataError, match="--force"):
        sd.generate(CORE, out, ref="v0.16.3", source=tmp_path / "repo")


def test_large_output_is_split_into_parts_and_force_replaces_old_parts(tmp_path):
    records, _ = sd.collect(CORE, make_repo(tmp_path / "repo"), "1", "c")
    out = tmp_path / "kb"
    parts = sd.write_files(CORE, records, out, part_bytes=300)
    assert len(parts) > 2 and [p["name"] for p in parts][0] == "pydpf_core_source-part-0001.jsonl"
    assert sum(p["records"] for p in parts) == len(records) == len(rec.dpf_core_documents(out))
    single = sd.write_files(CORE, records, out, force=True)
    assert [p["name"] for p in single] == ["pydpf_core_source.jsonl"]
    assert sorted(p.name for p in out.iterdir()) == ["pydpf_core_source.jsonl"]


def test_the_post_and_pydyna_files_have_the_names_records_py_reads(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sd.generate(sd.PROJECTS["pydpf-post"], tmp_path / "kb", ref="v0.12.0", source=repo)
    sd.generate(sd.PROJECTS["pydyna"], tmp_path / "kb", ref="v0.12.1", source=repo)
    post = rec.dpf_post_documents(tmp_path / "kb")
    assert post and post[0].metadata["source_project"] == "PyDPF-Post"
    assert [p.name for p in rec.api_files(tmp_path / "kb")] == ["pydyna_source.jsonl"]
    assert rec.api_documents(tmp_path / "kb")[0].metadata["class"] == "PyDyna source"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_clone_of_a_tag_records_the_commit(tmp_path):
    repo = make_repo(tmp_path / "repo")

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                              check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    git("tag", "v9.9.9")
    commit = git("rev-parse", "HEAD")
    manifest = sd.generate(CORE, tmp_path / "kb", ref="v9.9.9", repository=repo.as_uri())
    assert manifest["commit"] == commit and manifest["ref"] == "v9.9.9" and manifest["files_kept"] == 3
    with pytest.raises(sd.SourceDataError, match="git clone failed"):
        sd.generate(CORE, tmp_path / "kb2", ref="v0.0.0", repository=repo.as_uri())


def test_command_line(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    assert sd.main(["pydpf-core", "--out", str(tmp_path / "kb"), "--ref", "v1.0.0", "--source", str(repo)]) == 0
    assert "pydpf-core: v1.0.0" in capsys.readouterr().out
    assert sd.main(["pydpf-core", "--out", str(tmp_path / "kb"), "--ref", "v1.0.0", "--source", str(repo)]) == 1
    assert "--force" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        sd.main(["pydyna", "pydpf-core", "--out", str(tmp_path / "kb"), "--ref", "v1"])


# ---------------------------------------------------------------- index builder

class FakeCollection:
    def __init__(self, metadata, lose=0):
        self.metadata, self.rows, self.lose = metadata, [], lose

    def add(self, ids, documents, metadatas):
        assert len(ids) == len(documents) == len(metadatas)
        self.rows.extend(zip(ids, documents, metadatas))

    def count(self):
        return len(self.rows) - self.lose


class FakeClient:
    def __init__(self, existing=(), lose=0):
        self.collections = {name: FakeCollection({}) for name in existing}
        self.deleted, self.lose = [], lose

    def get_collection(self, name):
        if name not in self.collections:
            raise ValueError(f"Collection {name} does not exist")
        return self.collections[name]

    def delete_collection(self, name):
        self.deleted.append(name)
        del self.collections[name]

    def create_collection(self, name, embedding_function, metadata):
        self.collections[name] = FakeCollection(metadata, self.lose)
        return self.collections[name]


def test_build_collection_adds_every_record_in_batches_and_never_replaces_silently(tmp_path):
    sd.generate(CORE, tmp_path / "kb", ref="v0.16.3", source=make_repo(tmp_path / "repo"))
    documents = rec.dpf_core_documents(tmp_path / "kb")
    client = FakeClient(existing=["pydpf_post"])
    seen = []
    count = bi.build_collection(client, None, "pydpf_core", documents, batch=2,
                                progress=lambda done, total: seen.append(done))
    assert count == len(documents) and seen[-1] == len(documents) and len(seen) == -(-len(documents) // 2)
    built = client.collections["pydpf_core"]
    assert built.metadata == {"hnsw:space": "cosine"} and [r[0] for r in built.rows] == [d.id for d in documents]
    with pytest.raises(bi.IndexBuildError, match="--replace"):
        bi.build_collection(client, None, "pydpf_core", documents)
    bi.build_collection(client, None, "pydpf_core", documents, replace=True, space="l2")
    assert client.deleted == ["pydpf_core"] and "pydpf_post" in client.collections  # others untouched
    with pytest.raises(bi.IndexBuildError, match="expected"):
        bi.build_collection(FakeClient(lose=1), None, "pydpf_core", documents)
    with pytest.raises(bi.IndexBuildError, match="no records"):
        bi.build_collection(FakeClient(), None, "pydpf_core", [])
    with pytest.raises(bi.IndexBuildError, match="space"):
        bi.build_collection(FakeClient(), None, "pydpf_core", documents, space="dot")


def test_manifest_and_main_database_guard(tmp_path):
    sd.generate(CORE, tmp_path / "kb", ref="v0.16.3", source=make_repo(tmp_path / "repo"))
    db = tmp_path / "vector_db_dpf_core"
    db.mkdir()
    manifest = bi.write_manifest(db, "pydpf_core", 7, "BAAI/bge-small-en-v1.5", 384, "cosine",
                                 rec.dpf_core_files(tmp_path / "kb"))
    entry = manifest["collections"]["pydpf_core"]
    data = (tmp_path / "kb" / "pydpf_core_source.jsonl").read_bytes()
    assert entry["count"] == 7 and entry["dimension"] == 384
    assert entry["files"] == [{"name": "pydpf_core_source.jsonl", "bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()}]
    with pytest.raises(bi.IndexBuildError, match="main PyDyna search database"):
        bi.check_target(tmp_path / "kb", tmp_path / "vector_db", allow_main_db=False)
    bi.check_target(tmp_path / "kb", tmp_path / "vector_db", allow_main_db=True)
    bi.check_target(tmp_path / "kb", db, allow_main_db=False)


# ---------------------------------------------------------------- DPF-Core search

class FakeBackend:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def query(self, collection, text, n_results, where=None):
        self.calls.append((collection, n_results))
        return self.hits.get(collection, [])[:n_results]


def test_search_pydpf_core_uses_its_own_database_and_drops_tests(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        f"llm:\n  model_id: m\nexecution:\n  working_directory: '{tmp_path / 'work'}'\n")
    config = load_config(tmp_path / "config" / "config.yaml", env={}, cwd=tmp_path)
    assert pt.dpf_core_database_path(config) == pt.database_path(config).parent / "vector_db_dpf_core"
    hits = [Hit(id="dpfcore:tests/test_a.py#0", document="test", distance=0.1,
                metadata={"source_path": "tests/test_a.py"}),
            Hit(id="dpfcore:examples/a.py#0", document="binout energies", distance=0.2,
                metadata={"class": "PyDPF-Core source", "method": "examples/a.py#chunk-1",
                          "source_path": "examples/a.py", "chunk_index": 0})]
    backend = FakeBackend({"pydpf_core": hits})
    found = json.loads(pt.search_dpf_core(config, backend, "binout energy", 1))
    assert [h["source_path"] for h in found] == ["examples/a.py"] and found[0]["score"] == 0.8
    assert backend.calls == [("pydpf_core", 5)]
