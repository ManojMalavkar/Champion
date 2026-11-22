"""File helpers shared by the memory layers (track T8).

Every memory file is JSON, written atomically (temporary file in the same
folder, ``fsync``, ``os.replace``) with file mode 600 in folders with mode
700. Memory never keeps secrets: ``secret_reason`` finds keys, tokens and
password assignments in any text; layers that save what the user or the agent
chose (L2, L3) refuse such text, the automatic runtime state (L0) redacts it.

Record IDs look like ``REQ-20261121-0014``: prefix, local date, a number per
day. A new ID is claimed with ``O_EXCL``, so two processes never get the same one.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MAX_TEXT_CHARS = 4000
REDACTED = "[removed: looks like a secret]"

SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE), "a private key"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "an API key"),
    (re.compile(r"\bdapi[0-9a-f]{32}\b", re.IGNORECASE), "a Databricks token"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "a JWT token"),
    (re.compile(r"(?i)authorization\s*:\s*(bearer|basic)\s+\S+"), "an authorization header"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|token)\b\s*[:=]\s*\S{4,}"),
     "a password or key assignment"),
)


class MemoryRefused(ValueError):
    """A memory operation was refused; the message says why and what to do."""


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def secret_reason(value: Any) -> str | None:
    """What secret ``value`` (text or nested data) seems to contain, or None."""
    for text in _strings(value):
        for pattern, what in SECRET_PATTERNS:
            if pattern.search(text):
                return what
    return None


def refuse_secrets(value: Any, what: str = "this") -> None:
    reason = secret_reason(value)
    if reason:
        raise MemoryRefused(f"{what} contains {reason}; secrets are never saved in memory")


def redact(value: Any) -> Any:
    """Copy of ``value`` with every secret-looking string replaced (for automatic records)."""
    if isinstance(value, str):
        return REDACTED if secret_reason(value) else value
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def clean_text(value: Any, what: str, max_chars: int = MAX_TEXT_CHARS, *, required: bool = False) -> str:
    """Stripped text, refused when too long, empty (if required) or secret."""
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise MemoryRefused(f"{what} is empty")
    if len(text) > max_chars:
        raise MemoryRefused(f"{what} is {len(text)} characters; at most {max_chars}")
    refuse_secrets(text, what)
    return text


def cut(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f" [cut: {len(text) - max_chars} more characters]"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def real_home() -> Path:
    """The user's real home folder (not a changed ``HOME``), as for the access rules."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return Path(os.path.expanduser("~"))


def user_memory_root() -> Path:
    """Per-user memory folder: ``~/.champion/memory``."""
    return real_home() / ".champion" / "memory"


def ensure_dir(folder: Path) -> Path:
    folder = Path(folder)
    if folder.is_symlink():
        raise MemoryRefused(f"{folder} is a symbolic link; memory folders must be real folders")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    return folder


def write_json(path: Path, data: Any) -> Path:
    """Write ``data`` atomically (mode 600). A crash leaves the old file or the new one, never half."""
    path = Path(path)
    if path.is_symlink():
        raise MemoryRefused(f"{path} is a symbolic link; memory files are never written through links")
    ensure_dir(path.parent)
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


def read_json(path: Path) -> Any:
    path = Path(path)
    if path.is_symlink():
        raise MemoryRefused(f"{path} is a symbolic link; memory files are never read through links")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MemoryRefused(f"{path.name} is not valid JSON ({error.msg}, line {error.lineno})") from None


def claim_id(folder: Path, prefix: str, width: int, others: Iterable[Path] = ()) -> tuple[str, Path]:
    """Next free ``<prefix>-<YYYYMMDD>-<n>`` in ``folder`` (IDs in ``others`` count as used).

    The file ``<id>.json`` is created empty with ``O_EXCL`` (mode 600); the caller writes it at once.
    """
    folder = ensure_dir(folder)
    day = datetime.now().astimezone().strftime("%Y%m%d")
    stem = f"{prefix}-{day}-"
    used = 0
    for place in (folder, *others):
        for file in Path(place).glob(f"{stem}*.json") if Path(place).is_dir() else ():
            tail = file.stem[len(stem):]
            if tail.isdigit():
                used = max(used, int(tail))
    number = used + 1
    while True:
        record_id = f"{stem}{number:0{width}d}"
        path = folder / f"{record_id}.json"
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            number += 1
            continue
        return record_id, path
