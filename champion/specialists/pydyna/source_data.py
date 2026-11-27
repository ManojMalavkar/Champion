"""Generate the PyDyna knowledge source files from the Ansys GitHub repositories (track T1, T1.3b).

The old generator of ``pydyna_source-part-*.jsonl`` and ``pydpf_post_source.jsonl`` is gone
(docs/tracks/pydyna.md), and there is no PyDPF-Core data at all. This script writes those files
again, in the same record format, from a Git tag of the public repository:

==============  ==========================================  =============================
Project         Repository                                  Files
==============  ==========================================  =============================
``pydyna``      https://github.com/ansys/pydyna.git          ``pydyna_source[-part-*].jsonl``
``pydpf-post``  https://github.com/ansys/pydpf-post.git      ``pydpf_post_source[...]``
``pydpf-core``  https://github.com/ansys/pydpf-core.git      ``pydpf_core_source[...]``
==============  ==========================================  =============================

Run it on the machine that keeps the knowledge data (nothing is uploaded anywhere)::

    python -m champion.specialists.pydyna.source_data pydpf-core --out knowledge/pydyna/knowledge-base
    python -m champion.specialists.pydyna.source_data pydyna pydpf-post --out /tmp/new-kb

* The tag is ``v<installed version>`` of the package (ansys-dpf-core 0.16.3 -> ``v0.16.3``), so the
  data matches the API catalog; ``--ref`` gives another tag or branch, ``--source`` an existing
  checkout instead of cloning (``git clone --depth 1``).
* Files kept: the same rule as the search (``records.keep_code_path``): ``.py``, ``.rst``, ``.md``
  under ``src/``, ``doc/``, ``docs/``, ``examples/``; no tests, hidden folders, repository files or
  generated protobuf modules (``*_pb2.py``, ``*_pb2_grpc.py``).
  ``--exclude`` drops more (glob on the repository path).
* Chunks of at most 1800 characters (what the search tools show of a hit), cut at ``class`` /
  ``def`` lines (Python), headings (Markdown, reStructuredText), never inside a Markdown code
  block; a longer block is cut at line ends.
* Each record: ``class`` ("PyDPF-Core source" ...), ``method`` (``<path>#chunk-<n>``),
  ``description`` (the chunk text), ``source_project``, ``source_path``, ``chunk_index`` as the
  old files, plus ``start_line``, ``end_line``, ``version`` and ``commit``.
* Output over 40 MB is split into ``-part-0001`` ... files (as ``pydyna_source``). Existing files
  of the project are never overwritten without ``--force``. ``MANIFEST-<project>.json`` lists the
  repository, tag, commit, counts and the SHA-256 of every file.

Only the Python standard library and ``git`` are needed.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from champion.specialists.pydyna import records as rec

GENERATOR = "champion.specialists.pydyna.source_data 1"
CHUNK_CHARS = 1800
MAX_FILE_BYTES = 2 * 1024 * 1024
PART_BYTES = 40 * 1024 * 1024
GIT_TIMEOUT = 900  # seconds


@dataclass(frozen=True)
class Project:
    key: str
    repository: str
    distribution: str
    label: str  # the ``class`` of every record, as in the old files
    source_project: str
    stem: str  # output file name without .jsonl


PROJECTS = {p.key: p for p in (
    Project("pydyna", "https://github.com/ansys/pydyna.git", "ansys-dyna-core", "PyDyna source", "PyDyna",
            "pydyna_source"),
    Project("pydpf-post", "https://github.com/ansys/pydpf-post.git", "ansys-dpf-post", "PyDPF-Post source",
            "PyDPF-Post", "pydpf_post_source"),
    Project("pydpf-core", "https://github.com/ansys/pydpf-core.git", "ansys-dpf-core", "PyDPF-Core source",
            "PyDPF-Core", "pydpf_core_source"),
)}


class SourceDataError(Exception):
    """Generation cannot go on; the message says why."""


# ---------------------------------------------------------------- chunks

_PY_BOUNDARY = re.compile(r"^(?:    )?(?:@|class\s|def\s|async\s+def\s)")
_MD_HEADING = re.compile(r"^#{1,6}\s")
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+:.])\1{2,}\s*$")


def _block_starts(lines: list[str], suffix: str) -> list[int]:
    """0-based lines where a block starts: class / def (not after its decorator) or a heading."""
    starts, fence = [0], False
    for i, line in enumerate(lines):
        if suffix == ".md" and line.lstrip().startswith("```"):
            fence = not fence
            continue
        if i == 0 or fence:
            continue
        if suffix == ".py":
            if _PY_BOUNDARY.match(line) and not lines[i - 1].lstrip().startswith("@"):
                starts.append(i)
        elif suffix == ".md":
            if _MD_HEADING.match(line):
                starts.append(i)
        elif suffix == ".rst":
            if (line.strip() and i + 1 < len(lines) and _RST_UNDERLINE.match(lines[i + 1])
                    and len(lines[i + 1].strip()) >= len(line.strip())):
                starts.append(i)
    return starts


def chunk_text(text: str, suffix: str, limit: int = CHUNK_CHARS) -> list[tuple[int, int, str]]:
    """``(first line, last line, text)`` of each chunk (lines 1-based, inclusive); every chunk has at
    most ``limit`` characters; whitespace-only chunks are dropped."""
    lines = text.splitlines()
    if not lines:
        return []
    # a line longer than the limit becomes several segments of the same line number
    segments = [(n, line[k:k + limit]) for n, line in enumerate(lines)
                for k in (range(0, len(line), limit) if line else [0])]
    first_segment: dict[int, int] = {}
    for index, (n, _) in enumerate(segments):
        first_segment.setdefault(n, index)
    total = [0]
    for _, seg in segments:
        total.append(total[-1] + len(seg))

    def size(a: int, b: int) -> int:  # characters of segments a..b joined with newlines
        return total[b + 1] - total[a] + (b - a)

    starts = [first_segment[n] for n in _block_starts(lines, suffix.lower())]
    pieces: list[tuple[int, int]] = []  # each at most limit characters
    for k, start in enumerate(starts):
        end = (starts[k + 1] if k + 1 < len(starts) else len(segments)) - 1
        first = start
        for index in range(start, end + 1):
            if index > first and size(first, index) > limit:
                pieces.append((first, index - 1))
                first = index
        pieces.append((first, end))
    packed: list[tuple[int, int]] = []
    for first, last in pieces:
        if packed and size(packed[-1][0], last) <= limit:
            packed[-1] = (packed[-1][0], last)
        else:
            packed.append((first, last))
    result = []
    for first, last in packed:
        body = "\n".join(seg for _, seg in segments[first:last + 1]).strip("\n")
        if body.strip():
            result.append((segments[first][0] + 1, segments[last][0] + 1, body))
    return result


def file_records(project: Project, source_path: str, text: str, version: str, commit: str) -> list[dict]:
    suffix = Path(source_path).suffix
    return [{"class": project.label, "method": f"{source_path}#chunk-{index + 1}", "description": body,
             "source_project": project.source_project, "source_path": source_path, "chunk_index": index,
             "start_line": first, "end_line": last, "version": version, "commit": commit}
            for index, (first, last, body) in enumerate(chunk_text(text, suffix))]


def collect(project: Project, root: Path, version: str, commit: str,
            excludes: tuple[str, ...] = ()) -> tuple[list[dict], dict]:
    """Records of every kept file of a checkout, in path order, and the file counts."""
    root = Path(root)
    records: list[dict] = []
    stats = {"files_seen": 0, "files_kept": 0, "skipped": {}}
    paths = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        paths.extend(Path(folder) / name for name in files)
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        source_path = path.relative_to(root).as_posix()
        stats["files_seen"] += 1
        reason, text = None, ""
        if path.is_symlink() or not path.is_file():
            reason = "not a regular file"
        elif not rec.keep_code_path(source_path):
            reason = "not source, docs or examples"
        elif any(fnmatch.fnmatch(source_path, pattern) for pattern in excludes):
            reason = "excluded"
        elif path.stat().st_size > MAX_FILE_BYTES:
            reason = "larger than 2 MB"
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                reason = "not UTF-8"
        found = [] if reason else file_records(project, source_path, text, version, commit)
        if not reason and not found:
            reason = "empty"
        if reason:
            stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1
            continue
        stats["files_kept"] += 1
        records.extend(found)
    return records, stats


# ---------------------------------------------------------------- files

def _existing(project: Project, out_dir: Path) -> list[Path]:
    names = [out_dir / f"{project.stem}.jsonl", out_dir / f"MANIFEST-{project.key}.json"]
    return [p for p in names if p.exists()] + sorted(out_dir.glob(f"{project.stem}-part-*.jsonl"))


def write_files(project: Project, records: list[dict], out_dir: Path, part_bytes: int = PART_BYTES,
                force: bool = False) -> list[dict]:
    """Write the JSONL file(s); returns name, records, bytes and SHA-256 of each."""
    out_dir = Path(out_dir)
    existing = _existing(project, out_dir)
    if existing and not force:
        raise SourceDataError(f"{', '.join(p.name for p in existing)} already in {out_dir}; "
                              "use --force to replace them")
    parts: list[list[str]] = [[]]
    size = 0
    for record in records:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        length = len(line.encode("utf-8"))
        if parts[-1] and size + length > part_bytes:
            parts.append([])
            size = 0
        parts[-1].append(line)
        size += length
    names = ([f"{project.stem}.jsonl"] if len(parts) == 1
             else [f"{project.stem}-part-{k:04d}.jsonl" for k in range(1, len(parts) + 1)])
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in existing:  # with --force: old parts must not stay next to the new ones
        path.unlink()
    written = []
    for name, lines in zip(names, parts):
        data = "".join(lines).encode("utf-8")
        tmp = out_dir / f".{name}.tmp"
        tmp.write_bytes(data)
        tmp.replace(out_dir / name)
        written.append({"name": name, "records": len(lines), "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    return written


# ---------------------------------------------------------------- git

def _git(*args: str, cwd: Path | None = None) -> str:
    git = shutil.which("git")
    if git is None:
        raise SourceDataError("git is not installed; clone the repository yourself and give --source")
    try:
        done = subprocess.run([git, *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SourceDataError(f"git {args[0]} took longer than {GIT_TIMEOUT} s") from None
    if done.returncode != 0:
        raise SourceDataError(f"git {args[0]} failed: {done.stderr.strip()[-500:]}")
    return done.stdout.strip()


def default_ref(project: Project) -> str:
    try:
        return "v" + importlib.metadata.version(project.distribution)
    except importlib.metadata.PackageNotFoundError:
        raise SourceDataError(f"{project.distribution} is not installed here: give --ref (a tag such as v0.16.3) "
                              "so the data matches the version you use") from None


def _commit(root: Path) -> str:
    try:
        return _git("rev-parse", "HEAD", cwd=root)
    except SourceDataError:
        return "unknown"


# ---------------------------------------------------------------- one project

def generate(project: Project, out_dir: Path, ref: str | None = None, source: Path | None = None,
             repository: str | None = None, force: bool = False, part_bytes: int = PART_BYTES,
             excludes: tuple[str, ...] = ()) -> dict:
    """Write the files of one project and its manifest; returns the manifest."""
    out_dir = Path(out_dir)
    if _existing(project, out_dir) and not force:  # before a long clone
        raise SourceDataError(f"files of {project.key} already in {out_dir}; use --force to replace them")
    repository = repository or project.repository
    with tempfile.TemporaryDirectory(prefix=f"{project.key}-") as tmp:
        if source is None:
            ref = ref or default_ref(project)
            root = Path(tmp) / "repo"
            _git("clone", "--quiet", "--depth", "1", "--branch", ref, repository, str(root))
        else:
            root = Path(source)
            if not root.is_dir():
                raise SourceDataError(f"{root} is not a folder")
            ref = ref or "local"
        commit = _commit(root)
        version = ref[1:] if re.fullmatch(r"v\d[\w.\-]*", ref) else ref
        records, stats = collect(project, root, version, commit, excludes)
    if not records:
        raise SourceDataError(f"no source, docs or example files found for {project.key} (ref {ref})")
    files = write_files(project, records, out_dir, part_bytes, force)
    manifest = {"project": project.key, "repository": repository, "ref": ref, "version": version,
                "commit": commit, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "generator": GENERATOR, "chunk_chars": CHUNK_CHARS, "excludes": list(excludes),
                "records": len(records), **stats, "files": files}
    (out_dir / f"MANIFEST-{project.key}.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m champion.specialists.pydyna.source_data",
                                     description="Generate PyDyna knowledge source JSONL from the Ansys GitHub repositories.")
    parser.add_argument("projects", nargs="+", choices=[*PROJECTS, "all"])
    parser.add_argument("--out", required=True, type=Path, help="folder for the JSONL files and manifests")
    parser.add_argument("--ref", help="Git tag or branch (default: v<installed version>); one project only")
    parser.add_argument("--source", type=Path, help="existing checkout instead of cloning; one project only")
    parser.add_argument("--repository", help="other repository URL (a mirror); one project only")
    parser.add_argument("--exclude", action="append", default=[], help="glob of repository paths to leave out")
    parser.add_argument("--force", action="store_true", help="replace existing files of the project")
    args = parser.parse_args(argv)
    keys = list(PROJECTS) if "all" in args.projects else list(dict.fromkeys(args.projects))
    if len(keys) > 1 and (args.ref or args.source or args.repository):
        parser.error("--ref, --source and --repository need exactly one project")
    status = 0
    for key in keys:
        try:
            manifest = generate(PROJECTS[key], args.out, args.ref, args.source, args.repository, args.force,
                                excludes=tuple(args.exclude))
        except SourceDataError as exc:
            print(f"{key}: ERROR {exc}", file=sys.stderr)
            status = 1
            continue
        files = ", ".join(f"{f['name']} ({f['records']})" for f in manifest["files"])
        print(f"{key}: {manifest['ref']} {manifest['commit'][:10]}: {manifest['records']} records from "
              f"{manifest['files_kept']} of {manifest['files_seen']} files -> {files}")
    return status


if __name__ == "__main__":
    sys.exit(main())
