"""Streaming patch engine (step 4a): replace one exact block in a text file of
any size, without loading the file into memory. Used by ``file_edit`` and by
``/undo``; the L3 LS-DYNA model editor builds on it.

Two passes:

1. ``scan``: read the file in chunks of ``CHUNK`` bytes with an overlap of
   ``len(needle) - 1`` bytes, so a match across a chunk border is found.
   Every match is counted (overlapping ones too), with byte offset and line
   number. Memory: one chunk plus the needle.
2. ``apply_hunks`` (``apply_patch`` for one block): stream-copy the file to a
   temporary file in the same folder, writing each new block at its
   remembered offset (step 4d: several non-overlapping blocks in one pass);
   check that the bytes there are still the old blocks; ``fsync``; check that the file was not
   changed meanwhile (size, modification time, inode); atomic ``os.replace``.
   On any error, timeout or cancel the temporary file is removed and the
   original stays untouched. The SHA-256 of the new file is computed while
   writing (used by ``/undo`` to check nothing changed since).

Text handling: the file is read as bytes. ``sniff`` decides the encoding
(UTF-8, else Latin-1) and the line ending (LF or CRLF) from the first 64 KiB;
the old and new text are encoded the same way, so a match is exact bytes and
the file keeps its encoding and line endings. Files with NUL bytes in the
first 8 KiB are binary and refused.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
import uuid
from dataclasses import dataclass, field

from champion.tools.guard import ToolError

CHUNK = 1024 * 1024
SNIFF = 65536
MAX_OLD = 64 * 1024
MAX_NEW = 1024 * 1024
CONTEXT_LINES = 3
MAX_DIFF_LINES = 60


@dataclass(frozen=True)
class FileFormat:
    encoding: str  # "utf-8" or "latin-1"
    crlf: bool


@dataclass
class ScanResult:
    count: int = 0
    offsets: list[int] = field(default_factory=list)  # first matches only
    lines: list[int] = field(default_factory=list)
    size: int = 0


def sniff(fd: int) -> FileFormat:
    head = os.pread(fd, SNIFF, 0)
    if b"\x00" in head[:8192]:
        raise ToolError("the file looks like a binary file; only text files can be edited")
    try:
        head.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError as exc:
        # a multi-byte character cut at the end of the sample is still UTF-8
        encoding = "utf-8" if exc.start >= len(head) - 3 else "latin-1"
    lf = head.count(b"\n")
    crlf = lf > 0 and head.count(b"\r\n") * 2 > lf
    return FileFormat(encoding, crlf)


def encode(text: str, fmt: FileFormat, what: str) -> bytes:
    if not isinstance(text, str):
        raise ToolError(f"{what} must be text")
    text = text.replace("\r\n", "\n")
    if fmt.crlf:
        text = text.replace("\n", "\r\n")
    try:
        return text.encode(fmt.encoding)
    except UnicodeEncodeError:
        raise ToolError(f"{what} has characters that cannot be written in this file's encoding "
                        f"({fmt.encoding})") from None


def _check_deadline(deadline: float, what: str) -> None:
    if time.monotonic() > deadline:
        raise ToolError(f"{what} stopped: time limit reached; the file is unchanged")


def scan(fd: int, needle: bytes, deadline: float, keep: int = 5) -> ScanResult:
    """Count all (also overlapping) matches of ``needle``; remember the first ``keep``."""
    if not needle:
        raise ToolError("old_text must not be empty")
    result = ScanResult()
    carry = b""
    position = 0          # file offset of the next chunk
    newlines_before = 0   # newlines in the file before ``position``
    while True:
        chunk = os.pread(fd, CHUNK, position)
        if not chunk:
            break
        data = carry + chunk
        data_start = position - len(carry)
        data_newlines = newlines_before - carry.count(b"\n")
        index = data.find(needle)
        while index != -1:
            result.count += 1
            if len(result.offsets) < keep:
                result.offsets.append(data_start + index)
                result.lines.append(data_newlines + data.count(b"\n", 0, index) + 1)
            index = data.find(needle, index + 1)
        newlines_before += chunk.count(b"\n")
        position += len(chunk)
        carry = data[-(len(needle) - 1):] if len(needle) > 1 else b""
        _check_deadline(deadline, "scan")
    result.size = position
    return result


def preview(fd: int, offset: int, old: bytes, new: bytes, fmt: FileFormat, line: int, name: str) -> str:
    """A small unified-style diff of the changed lines with a few lines of context."""
    start = max(0, offset - 4096)
    window = os.pread(fd, offset - start + len(old) + 4096, start)
    rel = offset - start
    line_start = window.rfind(b"\n", 0, rel) + 1
    line_end = window.find(b"\n", rel + len(old))
    line_end = len(window) if line_end == -1 else line_end
    before_block = window[line_start:line_end]
    after_block = window[line_start:rel] + new + window[rel + len(old):line_end]
    head_lines = window[:line_start].split(b"\n")[:-1]
    if start > 0 and head_lines:
        head_lines = head_lines[1:]  # first line may be cut
    head = head_lines[-CONTEXT_LINES:]
    tail = window[line_end + 1:].split(b"\n")[:CONTEXT_LINES] if line_end < len(window) else []

    def text(raw: bytes) -> str:
        return raw.decode(fmt.encoding, errors="replace").rstrip("\r")

    out = [f"--- {name}", f"+++ {name}", f"@@ line {line} @@"]
    out += [f"  {text(x)}" for x in head]
    out += [f"- {text(x)}" for x in before_block.split(b"\n")]
    out += [f"+ {text(x)}" for x in after_block.split(b"\n")] if after_block or new else []
    out += [f"  {text(x)}" for x in tail]
    if len(out) > MAX_DIFF_LINES:
        out = out[:MAX_DIFF_LINES] + [f"... ({len(out) - MAX_DIFF_LINES} more diff lines not shown)"]
    return "\n".join(out)


def _copy(src: int, dst: int, start: int, length: int | None, digest, deadline: float) -> int:
    """Copy ``length`` bytes (None: to the end) from ``src`` at ``start``; returns bytes copied."""
    done = 0
    while length is None or done < length:
        size = CHUNK if length is None else min(CHUNK, length - done)
        data = os.pread(src, size, start + done)
        if not data:
            break
        _write_all(dst, data)
        digest.update(data)
        done += len(data)
        _check_deadline(deadline, "edit")
    return done


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def apply_patch(src_fd: int, dir_fd: int, name: str, *, offset: int, old: bytes, new: bytes,
                before: os.stat_result, deadline: float) -> tuple[int, str]:
    """Write the patched file atomically; returns (new size, SHA-256 of the new file)."""
    return apply_hunks(src_fd, dir_fd, name, [(offset, old, new)], before=before, deadline=deadline)


def apply_hunks(src_fd: int, dir_fd: int, name: str, hunks: list[tuple[int, bytes, bytes]], *,
                before: os.stat_result, deadline: float) -> tuple[int, str]:
    """Write all ``(offset, old, new)`` blocks in one atomic replacement.

    ``hunks`` are sorted by offset and do not overlap; offsets are in the current file.
    Returns (new size, SHA-256 of the new file). On any error nothing is written.
    """
    temp_name = f".{name}.champion-{uuid.uuid4().hex[:8]}.tmp"
    temp = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                   0o600, dir_fd=dir_fd)
    digest = hashlib.sha256()
    try:
        position, written = 0, 0
        for offset, old, new in hunks:
            if offset < position:
                raise ToolError("internal error: edit blocks overlap; nothing was written")
            if _copy(src_fd, temp, position, offset - position, digest, deadline) != offset - position:
                raise ToolError("the file changed since the scan (it is shorter); nothing was written")
            if os.pread(src_fd, len(old), offset) != old:
                raise ToolError("the file changed since the scan (the old text moved); nothing was written")
            _write_all(temp, new)
            digest.update(new)
            written += offset - position + len(new)
            position = offset + len(old)
        written += _copy(src_fd, temp, position, None, digest, deadline)
        os.fsync(temp)
        os.fchmod(temp, stat.S_IMODE(before.st_mode))
        now = os.fstat(src_fd)
        current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns) \
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise ToolError("the file changed while it was being edited; nothing was written")
        os.close(temp)
        temp = -1
        os.replace(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except BaseException:
        if temp != -1:
            os.close(temp)
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
    return written, digest.hexdigest()


def line_range(fd: int, start: int, end: int, deadline: float) -> tuple[int, int, int]:
    """Byte range ``[a, b)`` of lines ``start``..``end`` (1-based, inclusive; ``b`` after the
    last newline). Streaming; returns (a, b, number of lines in the file if the range was
    not found, else 0)."""
    position, newlines, last_end = 0, 0, 0
    a = 0 if start == 1 else None
    while True:
        chunk = os.pread(fd, CHUNK, position)
        if not chunk:
            break
        count = chunk.count(b"\n")
        if newlines + count >= start - 1 and (a is None or newlines + count >= end):
            index = -1
            while True:
                index = chunk.find(b"\n", index + 1)
                if index == -1:
                    break
                newlines += 1
                if newlines == start - 1:
                    a = position + index + 1
                if newlines == end:
                    return a, position + index + 1, 0
        else:
            newlines += count
        if count:
            last_end = position + chunk.rfind(b"\n") + 1
        position += len(chunk)
        _check_deadline(deadline, "line search")
    total = newlines + (1 if position > last_end else 0)
    if position > last_end and end == newlines + 1 and a is not None:
        return a, position, 0  # last line without a newline
    return -1, -1, total


def file_sha256(fd: int, deadline: float) -> str:
    digest, position = hashlib.sha256(), 0
    while True:
        data = os.pread(fd, CHUNK, position)
        if not data:
            return digest.hexdigest()
        digest.update(data)
        position += len(data)
        _check_deadline(deadline, "hash")
