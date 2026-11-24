"""Checks of the knowledge layer (M1): ``champion kb status`` and ``champion doctor``.

For every declared knowledge base: the root (inside the project, no link), each database
folder (``chroma.sqlite3``, ``MANIFEST.json``), each collection (present, count equal to
the manifest, model equal to the declared one) and each embedding model (in the local
cache). With ``verify=True`` the source files are also compared with the SHA-256 in the
manifest. Missing data is a WARN (the agent then says it cannot search); a wrong count,
model or changed file is a FAIL. Opening a database follows the read-only rules of
``store`` (fingerprint before and after).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any

from champion.knowledge.embedding import model_in_cache
from champion.knowledge.manifest import file_problems, read_manifest
from champion.knowledge.registry import knowledge_specs
from champion.knowledge.root import KnowledgeError, resolve_root
from champion.knowledge.store import SQLITE, store_for

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass(frozen=True)
class StatusLine:
    name: str
    status: str
    detail: str


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def knowledge_status(config: Any, agent: str | None = None, verify: bool = False,
                     open_databases: bool = True) -> list[StatusLine]:
    specs = [s for s in knowledge_specs() if agent is None or s.agent == agent]
    if not specs:
        if agent is not None:
            return [StatusLine("Knowledge", FAIL, f"no knowledge base is declared for {agent}")]
        return [StatusLine("Knowledge", OK, "no agent declares a knowledge base")]
    lines: list[StatusLine] = []
    chroma = _has("chromadb")
    models: dict[str, list[str]] = {}
    for spec in specs:
        for item in spec.collections:  # the model is checked even when the database is missing
            models.setdefault(item.model, []).append(f"{spec.agent}/{item.name}")
        try:
            root = resolve_root(config, spec.agent, spec.config_section)
        except KnowledgeError as exc:
            lines.append(StatusLine(f"Knowledge {spec.agent}", FAIL, str(exc)))
            continue
        if not root.path.is_dir():
            lines.append(StatusLine(f"Knowledge {spec.agent}", WARN, f"root {root.path} does not exist; "
                                    "put the knowledge-base and vector_db folders there"))
            continue
        lines.append(StatusLine(f"Knowledge {spec.agent}", OK, str(root.path)))
        for database in sorted({c.database for c in spec.collections}):
            label = f"Knowledge {spec.agent}/{database}"
            items = [c for c in spec.collections if c.database == database]
            db = root.db_dir(database)
            if db.is_symlink():
                lines.append(StatusLine(label, FAIL, f"{db} is a symbolic link; links are not followed"))
                continue
            if not (db / SQLITE).is_file():
                lines.append(StatusLine(label, WARN, f"no {SQLITE} in {db}; build with champion kb build "
                                        f"--agent {spec.agent}"))
                continue
            try:
                manifest = read_manifest(db)
            except KnowledgeError as exc:
                lines.append(StatusLine(label, FAIL, str(exc)))
                continue
            lines.append(StatusLine(label, OK if manifest is not None else WARN,
                                    str(db) if manifest is not None
                                    else f"{db}: no MANIFEST.json, so counts and model are not checked"))
            store = store_for(db, bool(config.get("knowledge.verify_hash", True)))
            for item in items:
                name = f"Collection {spec.agent}/{item.name}"
                info = (manifest or {}).get(item.name)
                problems = []
                if manifest is not None and info is None:
                    problems.append("not in MANIFEST.json")
                if info is not None and info.model != item.model:
                    problems.append(f"built with {info.model}, the agent uses {item.model}")
                if info is not None and verify:
                    problems += file_problems(info, root.kb_dir)
                count = None
                if open_databases and chroma:
                    try:
                        count = store.count(item.name)
                    except KnowledgeError as exc:
                        problems.append(str(exc))
                    if count is not None and info is not None and count != info.count:
                        problems.append(f"{count} entries, MANIFEST.json says {info.count}")
                if problems:
                    lines.append(StatusLine(name, FAIL, "; ".join(problems)))
                    continue
                facts = [f"{count} entries" if count is not None else
                         ("not opened (chromadb not installed)" if not chroma else "not opened")]
                facts.append(f"model {item.model}" + (f" ({info.dimension}, {info.space})" if info else ""))
                if verify and info is not None:
                    facts.append(f"{len(info.files)} source file(s) match")
                lines.append(StatusLine(name, OK, ", ".join(facts)))
    st = _has("sentence_transformers")
    for model in sorted(models):
        used = ", ".join(models[model])
        cached = model_in_cache(model)
        if not st:
            lines.append(StatusLine(f"Embedding {model}", WARN, f"sentence-transformers not installed "
                                    f"(pip install -e '.[knowledge]'); used by {used}"))
        elif cached is False:
            lines.append(StatusLine(f"Embedding {model}", WARN, f"not in the local Hugging Face cache; used by {used}"))
        else:
            lines.append(StatusLine(f"Embedding {model}", OK, f"in the local cache; used by {used}"
                                    if cached else f"used by {used}"))
    return lines


def render_lines(lines: list[StatusLine]) -> str:
    width = max((len(line.name) for line in lines), default=0)
    return "\n".join(f"{line.status:<4}  {line.name:<{width}}  {line.detail}" for line in lines)
