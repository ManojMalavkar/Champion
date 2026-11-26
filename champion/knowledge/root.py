"""Knowledge root of an agent (M1).

The root is ``<section>.knowledge_root`` from the config, or ``knowledge/<agent>``
in the project folder when the key is empty or missing. A root must be inside the
project folder (PLAN.md, "Knowledge base layout") and is not reached through a
symbolic link. The root itself is never written by the search side; only
``champion kb build`` (the user, on the command line) writes into it.

Store folders in the root are named ``index`` or ``index_<name>`` (CR-4). Folders named
``vector_db`` / ``vector_db_<name>`` are old Chroma databases: they are never opened or
written and cannot be named as a store; ``champion kb status`` lists them as backups.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KB_FOLDER = "knowledge-base"
DEFAULT_DATABASE = "index"
DATABASE_NAME = re.compile(r"^index(_[a-z0-9_]{1,40})?$")
OLD_DATABASE_NAME = re.compile(r"^vector_db(_[a-z0-9_]{1,40})?$")  # old Chroma databases (backups)
AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


class KnowledgeError(Exception):
    """The knowledge root, database or model cannot be used; the message says what to do."""


@dataclass(frozen=True)
class KnowledgeRoot:
    agent: str
    path: Path
    project: Path

    @property
    def kb_dir(self) -> Path:
        return self.path / KB_FOLDER

    def db_dir(self, name: str = DEFAULT_DATABASE) -> Path:
        if not DATABASE_NAME.match(name or ""):
            raise KnowledgeError(f"store folder {name!r}: use index or index_<name> "
                                 "(lowercase letters, digits, _)")
        return self.path / name

    def _folders(self, pattern: re.Pattern) -> list[str]:
        if not self.path.is_dir():
            return []
        return sorted(p.name for p in self.path.iterdir()
                      if pattern.match(p.name) and p.is_dir() and not p.is_symlink())

    def databases(self) -> list[str]:
        """Store folders that exist in the root (links are left out)."""
        return self._folders(DATABASE_NAME)

    def old_databases(self) -> list[str]:
        """Old Chroma database folders in the root (backups; never opened)."""
        return self._folders(OLD_DATABASE_NAME)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return path != folder


def _first_link(path: Path, project: Path) -> Path | None:
    """The first existing part of ``path`` below ``project`` that is a symbolic link."""
    current = project
    for part in path.relative_to(project).parts:
        current = current / part
        if current.is_symlink():
            return current
        if not current.exists():
            return None
    return None


def resolve_root(config: Any, agent: str, section: str | None = None) -> KnowledgeRoot:
    """The checked knowledge root of ``agent`` (``section``: config section with ``knowledge_root``)."""
    if not AGENT_NAME.match(agent or ""):
        raise KnowledgeError(f"agent name {agent!r} is not valid")
    project = Path(config.root).resolve()
    key = f"{section or agent}.knowledge_root"
    value = config.get(key)
    path = Path(os.path.expanduser(str(value))) if value else project / "knowledge" / agent
    if not path.is_absolute():
        path = project / path
    path = Path(os.path.normpath(path))  # no resolve(): a link must be seen, not followed
    if not _inside(path, project):
        raise KnowledgeError(f"{agent}: knowledge root {path} is outside the project folder {project}; "
                             f"set {key} to a folder inside it (default knowledge/{agent})")
    link = _first_link(path, project)
    if link is not None:
        raise KnowledgeError(f"{agent}: knowledge root {path} goes through the symbolic link {link}; "
                             "links are not followed")
    return KnowledgeRoot(agent, path, project)
