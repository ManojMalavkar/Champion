"""Safe process executor (step 4a, PLAN 4a.4). It runs what the policy allowed
and decides nothing itself.

* ``shell=False`` always: the argument list goes to ``execve`` as it is, so
  ``;``, ``|``, ``$()``, ``>`` or ``*`` in an argument are plain characters;
* the program is looked up only in ``/usr/bin`` and ``/bin`` (never the
  user's ``PATH``, never the working folder);
* clean environment: ``PATH=/usr/bin:/bin``, ``LANG``/``LC_ALL=C.UTF-8``,
  ``TZ`` and an empty temporary ``HOME``; nothing else, so no
  ``DATABRICKS_TOKEN``, ``OPENAI_API_KEY`` or ``*_TOKEN`` reaches the child;
* standard input is empty (``/dev/null``); a pipeline connects its steps;
* every command runs in its own process group; at the time limit the whole
  group gets SIGTERM, then SIGKILL after 2 s (children included);
* ``setrlimit`` per process: CPU seconds, address space (memory), size of
  files it writes, no core dumps;
* output is read while the command runs and kept up to the cap (the rest is
  counted and dropped, so a huge output never fills memory); private key
  blocks and token-like strings are hidden before the text is returned.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

from champion.core.config import Config
from champion.tools.guard import ToolError

SYSTEM_BIN = ("/usr/bin", "/bin")
SAFE_PATH = "/usr/bin:/bin"
KILL_GRACE = 2.0
REDACTIONS = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
     "[private key hidden by Champion]"),
    (re.compile(r"\bdapi[0-9a-f]{32}\b"), "[token hidden by Champion]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[key hidden by Champion]"),
)


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float
    cpu_seconds: int
    memory_bytes: int
    file_bytes: int
    output_chars: int

    @classmethod
    def from_config(cls, config: Config) -> "Limits":
        return cls(timeout_seconds=float(config.get("execution.timeout_seconds")),
                   cpu_seconds=int(config.get("execution.max_cpu_seconds")),
                   memory_bytes=int(config.get("execution.max_memory_bytes")),
                   file_bytes=int(config.get("execution.max_command_file_bytes")),
                   output_chars=int(config.get("execution.max_output_chars")))


@dataclass
class CommandResult:
    exit_codes: list[int]
    stdout: str
    stderr: str
    seconds: float
    output_bytes: int
    timed_out: bool = False
    truncated: bool = False
    redacted: bool = False
    executables: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """The first non-zero exit code of the steps (like ``pipefail``), else 0."""
        return next((code for code in self.exit_codes if code != 0), 0)


def resolve(command: str) -> str:
    """Absolute path of ``command`` in the system folders only."""
    for folder in SYSTEM_BIN:
        candidate = os.path.join(folder, command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise ToolError(f"'{command}' is not installed on this machine (looked in {', '.join(SYSTEM_BIN)})")


def clean_env(home: str) -> dict[str, str]:
    return {"PATH": SAFE_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": os.environ.get("TZ", "UTC"),
            "HOME": home}


def redact(text: str) -> tuple[str, bool]:
    changed = False
    for pattern, replacement in REDACTIONS:
        text, count = pattern.subn(replacement, text)
        changed = changed or count > 0
    return text, changed


def _preexec(limits: Limits, group: list[int]):
    import resource

    def setup() -> None:
        try:
            os.setpgid(0, group[0] if group else 0)
        except OSError:
            os.setpgid(0, 0)
        for name, value in (("RLIMIT_CPU", limits.cpu_seconds), ("RLIMIT_AS", limits.memory_bytes),
                            ("RLIMIT_FSIZE", limits.file_bytes), ("RLIMIT_CORE", 0)):
            kind = getattr(resource, name, None)
            if kind is not None:
                try:
                    resource.setrlimit(kind, (value, value))
                except (ValueError, OSError):
                    pass
    return setup


class _Reader(threading.Thread):
    """Read a pipe to the end, keeping at most ``keep`` bytes."""

    def __init__(self, pipe, keep: int) -> None:
        super().__init__(daemon=True)
        self.pipe, self.keep = pipe, keep
        self.data = bytearray()
        self.total = 0

    def run(self) -> None:
        try:
            while True:
                chunk = self.pipe.read(65536)
                if not chunk:
                    break
                self.total += len(chunk)
                room = self.keep - len(self.data)
                if room > 0:
                    self.data += chunk[:room]
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass


def _kill_group(pgid: int, processes: list[subprocess.Popen]) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        end = time.monotonic() + KILL_GRACE
        while time.monotonic() < end and any(p.poll() is None for p in processes):
            time.sleep(0.05)
        if all(p.poll() is not None for p in processes):
            return


def run_steps(steps: list[list[str]], cwd: str, limits: Limits,
              extra_env: dict[str, str] | None = None) -> CommandResult:
    """Run one command or a pipeline (stdout of each step to the next); never a shell.

    ``extra_env`` is only used by run_check (``coder.check_env``); it never replaces
    the fixed variables of the clean environment."""
    executables = [resolve(argv[0]) for argv in steps]
    home = tempfile.mkdtemp(prefix="champion-home-")
    env = clean_env(home)
    for name, value in (extra_env or {}).items():
        env.setdefault(name, value)
    keep = max(limits.output_chars * 4, 4096)
    processes: list[subprocess.Popen] = []
    readers: list[_Reader] = []
    group: list[int] = []
    started = time.monotonic()
    timed_out = False
    try:
        previous = None
        for index, (argv, exe) in enumerate(zip(steps, executables)):
            last = index == len(steps) - 1
            process = subprocess.Popen([exe, *argv[1:]], executable=exe, cwd=cwd, env=env, shell=False,
                                       stdin=previous if previous is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                                       preexec_fn=_preexec(limits, group))
            if not group:
                group.append(process.pid)
            if previous is not None:
                previous.close()  # the next step owns it now (SIGPIPE works)
            processes.append(process)
            readers.append(_Reader(process.stderr, keep // 4))
            readers[-1].start()
            previous = None if last else process.stdout
        out = _Reader(processes[-1].stdout, keep)
        out.start()
        deadline = started + limits.timeout_seconds
        for process in processes:
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(remaining, 0.01))
            except subprocess.TimeoutExpired:
                timed_out = True
                break
        if timed_out:
            _kill_group(group[0], processes)
        for process in processes:
            try:
                process.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        out.join(KILL_GRACE)
        for reader in readers:
            reader.join(KILL_GRACE)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
        shutil.rmtree(home, ignore_errors=True)
    seconds = time.monotonic() - started
    stdout = out.data.decode("utf-8", errors="replace")
    stderr = "\n".join(r.data.decode("utf-8", errors="replace").rstrip() for r in readers if r.data)
    stdout, hidden_out = redact(stdout)
    stderr, hidden_err = redact(stderr)
    truncated = out.total > len(out.data) or len(stdout) > limits.output_chars
    return CommandResult(exit_codes=[p.returncode for p in processes], stdout=stdout[:limits.output_chars],
                         stderr=stderr[:limits.output_chars // 4], seconds=seconds,
                         output_bytes=out.total + sum(r.total for r in readers), timed_out=timed_out,
                         truncated=truncated, redacted=hidden_out or hidden_err, executables=executables)
