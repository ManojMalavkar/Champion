"""Command policy (step 4a, PLAN 4a.2 / 4a.3): which commands, options and paths
the shell tools may use. This module only *decides*; ``commands.py`` runs.

A command is an argument list (``["find", ".", "-name", "*.k"]``), never a
shell string. Rules:

* the command must be in ``RULES`` and given by name (no ``/``); commands in
  ``NEVER`` (shells, interpreters, ``sudo``, network, ``xargs``, ``env``,
  ``awk``, ``sed`` ...) are refused with their reason; anything else unknown
  is refused too (deny by default);
* every option must be in the rule's allowed set, exactly as written (no
  abbreviations); options that run programs, follow links, delete, read file
  lists or read contents recursively without Champion's checks are refused
  with a reason; unknown options are refused;
* GNU option order is kept: options may come after operands, ``--`` ends the
  options (the environment never sets ``POSIXLY_CORRECT``);
* every path (operands and path-valued options) goes through the step 2
  ``check_path`` (scope, write right, secrets, system folders, links) with
  the right the command needs: read, write, or delete (= write plus never
  the working folder itself or one of its parents);
* wildcards (``*.k``) in path operands are expanded by Champion inside the
  checked folder (last path part only; secret names and hidden files are
  left out unless the pattern starts with ``.``), never by a shell;
* the command's class decides the level it needs: ``read`` (any level),
  ``write`` (1.2 / 2.2 / custom with write; no prompt, recorded in the change
  list), ``delete`` (write level and the user's approval every time).

Known limits (written in docs/steps/04a): a command may still open a path
between the check and its own use (time-of-check race), and recursive copies
into an existing destination folder may follow links already present there.
The sandbox of later phases removes these.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from champion.policy.levels import AccessLevel
from champion.policy.scope import PolicyDenied, check_path
from champion.policy.sensitive import name_reason

CLASSES = ("read", "write", "delete", "process_control", "blocked")
MAX_ARGS = 200
MAX_ARG_CHARS = 4096
MAX_GLOB = 1000
GLOB = re.compile(r"[*?\[]")

FOLLOW = "follows symbolic links"
FILES_FROM = "reads a list of file names from a file (those paths could not be checked)"
RECURSIVE_READ = ("reads file contents recursively without Champion's secret checks; use filesystem_grep "
                  "(or name the files)")
INTERACTIVE = "asks questions on the terminal (there is none)"
RUNS_PROGRAM = "runs another program"
ARGS_SHOWN = "shows full command lines, which can contain secrets; use process_list or -o pid,comm,..."
HPC = "is an HPC scheduler command; these are handled by the HPC agent (a later step), not by the Linux agent"


@dataclass(frozen=True)
class CommandRule:
    command: str
    command_class: str
    description: str
    flags: frozenset = frozenset()
    values: dict = field(default_factory=dict)      # option -> kind of value
    forbidden: dict = field(default_factory=dict)   # option -> reason
    operands: str = "read"   # read | write | none | pattern+read | copy | move | delete | mode+write | find | tar
    min_operands: int = 0
    max_operands: int | None = None
    approval: str = "never"  # never | always
    network: bool = False


RULES: dict[str, CommandRule] = {}


def _rule(command: str, command_class: str, description: str, flags: str = "", values: dict | None = None,
          forbidden: dict | None = None, **options) -> None:
    RULES[command] = CommandRule(command, command_class, description, frozenset(flags.split()), values or {},
                                 forbidden or {}, **options)


# ---------------------------------------------------------------- read commands

_rule("ls", "read", "list folders",
      "-l -a -A -h -R -t -S -r -1 -d -F -i -s -n -g -o -c -u -U -X -v -p --all --almost-all --human-readable "
      "--recursive --reverse --directory --classify --inode --size --numeric-uid-gid --full-time",
      {"--color": "text", "--sort": "text", "--time-style": "text", "--block-size": "text", "-w": "int",
       "-I": "text", "--ignore": "text"},
      {"-L": FOLLOW, "--dereference": FOLLOW, "-H": FOLLOW, "--dereference-command-line": FOLLOW})
_rule("find", "read", "find files by name, type, size, age", operands="find")
_rule("du", "read", "disk usage of files and folders",
      "-s -h -a -c -k -m -b -x -S --summarize --human-readable --all --total --apparent-size --one-file-system "
      "--bytes --separate-dirs --si",
      {"-d": "int", "--max-depth": "int", "--exclude": "text", "-t": "text", "--threshold": "text",
       "-B": "text", "--block-size": "text"},
      {"-L": FOLLOW, "--dereference": FOLLOW, "-D": FOLLOW, "-H": FOLLOW, "--dereference-args": FOLLOW,
       "--files0-from": FILES_FROM, "-X": FILES_FROM, "--exclude-from": FILES_FROM})
_rule("df", "read", "free disk space",
      "-h -H -T -i -k -P -a -l --human-readable --si --print-type --inodes --portability --all --local --total",
      {"--output": "text", "-t": "text", "-x": "text", "--type": "text", "--exclude-type": "text",
       "-B": "text", "--block-size": "text"})
_rule("stat", "read", "file details", "-f -t --file-system --terse",
      {"-c": "text", "--format": "text", "--printf": "text"},
      {"-L": FOLLOW, "--dereference": FOLLOW}, min_operands=1)
_rule("file", "read", "file type", "-b -i -k -N -E --brief --mime --mime-type --mime-encoding",
      {}, {"-L": FOLLOW, "--dereference": FOLLOW, "-f": FILES_FROM, "--files-from": FILES_FROM,
           "-m": "uses another magic file", "--magic-file": "uses another magic file",
           "-z": "opens compressed files", "-Z": "opens compressed files", "-C": "writes a magic file",
           "-s": "reads devices"}, min_operands=1)
_COUNT = {"-n": "count", "-c": "count", "--lines": "count", "--bytes": "count"}
_rule("head", "read", "first lines of files", "-q -v -z --quiet --silent --verbose", dict(_COUNT))
_rule("tail", "read", "last lines of files", "-q -v -z --quiet --silent --verbose", dict(_COUNT),
      {"-f": "waits for new lines forever", "-F": "waits for new lines forever",
       "--follow": "waits for new lines forever", "--retry": "waits forever", "--pid": "waits for a process",
       "-s": "only used with --follow", "--sleep-interval": "only used with --follow"})
_rule("wc", "read", "count lines, words, bytes",
      "-l -w -c -m -L --lines --words --bytes --chars --max-line-length", {}, {"--files0-from": FILES_FROM})
_rule("grep", "read", "find text in named files",
      "-i -y -n -c -l -L -v -w -x -F -E -G -P -H -h -o -s -q -a -b -z -T -I --ignore-case --line-number "
      "--count --files-with-matches --files-without-match --invert-match --word-regexp --line-regexp "
      "--fixed-strings --extended-regexp --basic-regexp --perl-regexp --with-filename --no-filename "
      "--only-matching --no-messages --quiet --silent --text --byte-offset --initial-tab",
      {"-m": "int", "--max-count": "int", "-A": "int", "-B": "int", "-C": "int", "--after-context": "int",
       "--before-context": "int", "--context": "int", "-e": "text", "--regexp": "text", "-f": "path_read",
       "--file": "path_read", "--color": "text", "--colour": "text", "--label": "text"},
      {"-r": RECURSIVE_READ, "-R": RECURSIVE_READ, "--recursive": RECURSIVE_READ,
       "--dereference-recursive": RECURSIVE_READ, "-d": RECURSIVE_READ, "--directories": RECURSIVE_READ,
       "--include": RECURSIVE_READ, "--exclude": RECURSIVE_READ, "--exclude-dir": RECURSIVE_READ,
       "--exclude-from": FILES_FROM, "-D": "reads devices", "--devices": "reads devices"},
      operands="pattern+read")
_rule("sort", "read", "sort lines",
      "-n -r -u -h -f -V -b -s -g -M -d -i -c -C -z --numeric-sort --reverse --unique --human-numeric-sort "
      "--ignore-case --version-sort --stable --general-numeric-sort --check",
      {"-k": "text", "--key": "text", "-t": "text", "--field-separator": "text", "-S": "text",
       "--buffer-size": "text", "--parallel": "int"},
      {"-o": "writes a file", "--output": "writes a file", "--compress-program": RUNS_PROGRAM,
       "-T": "writes temporary files elsewhere", "--temporary-directory": "writes temporary files elsewhere",
       "--files0-from": FILES_FROM, "--random-source": "reads another file", "-R": "reads a random source",
       "--random-sort": "reads a random source"})
_rule("uniq", "read", "repeated lines", "-c -d -u -i -D -z --count --repeated --unique --ignore-case",
      {"-f": "int", "-s": "int", "-w": "int", "--skip-fields": "int", "--skip-chars": "int",
       "--check-chars": "int"}, max_operands=1)
_rule("cut", "read", "columns of lines", "-s -n -z --only-delimited --complement",
      {"-d": "text", "-f": "text", "-c": "text", "-b": "text", "--delimiter": "text", "--fields": "text",
       "--characters": "text", "--bytes": "text", "--output-delimiter": "text"})
_rule("diff", "read", "compare two files",
      "-u -q -s -w -b -B -i -y -a -t -T --brief --side-by-side --ignore-all-space --ignore-space-change "
      "--ignore-blank-lines --ignore-case --report-identical-files --text --strip-trailing-cr --unified",
      {"-U": "int", "-C": "int", "-W": "int", "--width": "int", "--label": "text"},
      {"-r": RECURSIVE_READ, "--recursive": RECURSIVE_READ, "-N": RECURSIVE_READ, "--new-file": RECURSIVE_READ,
       "-x": FILES_FROM, "-X": FILES_FROM, "--exclude-from": FILES_FROM},
      min_operands=2, max_operands=2)
_SUM_FORBIDDEN = {"-c": "reads file names from a checksum file (those paths could not be checked)",
                  "--check": "reads file names from a checksum file (those paths could not be checked)"}
_rule("md5sum", "read", "MD5 checksums", "-b -t -z --binary --text --tag --zero", {}, dict(_SUM_FORBIDDEN),
      min_operands=1)
_rule("sha256sum", "read", "SHA-256 checksums", "-b -t -z --binary --text --tag --zero", {},
      dict(_SUM_FORBIDDEN), min_operands=1)
_rule("ps", "read", "processes (no command lines)", "-e -A --no-headers",
      {"-u": "text", "-U": "text", "--user": "text", "-p": "text", "--pid": "text", "-C": "text",
       "-o": "ps_format", "--format": "ps_format", "--sort": "ps_sort"},
      {"-f": ARGS_SHOWN, "-F": ARGS_SHOWN, "-l": ARGS_SHOWN, "-w": ARGS_SHOWN, "-ww": ARGS_SHOWN,
       "-j": ARGS_SHOWN, "-x": ARGS_SHOWN}, operands="none")
_rule("free", "read", "memory use",
      "-h -b -k -m -g -t -w -l --human --bytes --kilo --mega --giga --total --wide --si", {"-c": "int"},
      {"-s": "repeats forever", "--seconds": "repeats forever"}, operands="none")
_rule("uptime", "read", "time since boot and load", "-p -s --pretty --since", operands="none")
_rule("nproc", "read", "number of CPUs", "--all", {"--ignore": "int"}, operands="none")
_rule("lscpu", "read", "CPU details", "-J --json -e --extended -p --parse -a -b -c -x --hex -y",
      operands="none")
_rule("nvidia-smi", "read", "GPU status", "-L -q --list-gpus --query",
      {"--query-gpu": "text", "--query-compute-apps": "text", "--format": "text", "-i": "text", "--id": "text",
       "-d": "text"}, operands="none")

# ---------------------------------------------------------------- write commands (no prompt, change list)

_rule("mkdir", "write", "create folders", "-p -v --parents --verbose", {"-m": "mode", "--mode": "mode"},
      operands="write", min_operands=1)
_rule("touch", "write", "create empty files or set times", "-a -m -c --no-create",
      {"-d": "text", "--date": "text", "-t": "text", "-r": "path_read", "--reference": "path_read"},
      {"-h": "changes symbolic links", "--no-dereference": "changes symbolic links"},
      operands="write", min_operands=1)
_rule("cp", "write", "copy files",
      "-r -R -p -v -n -u -T -a -P --recursive --preserve --verbose --no-clobber --update --no-target-directory "
      "--archive --no-dereference",
      {"-t": "path_write", "--target-directory": "path_write"},
      {"-L": FOLLOW, "--dereference": FOLLOW, "-H": FOLLOW, "-s": "creates symbolic links",
       "--symbolic-link": "creates symbolic links", "-l": "creates hard links", "--link": "creates hard links",
       "-i": INTERACTIVE, "--interactive": INTERACTIVE, "-f": "deletes destinations it cannot open",
       "--force": "deletes destinations it cannot open", "--remove-destination": "deletes the destination first"},
      operands="copy", min_operands=1)
_rule("mv", "write", "move or rename files", "-n -v -u -T --no-clobber --verbose --update --no-target-directory",
      {"-t": "path_write", "--target-directory": "path_write"},
      {"-i": INTERACTIVE, "--interactive": INTERACTIVE, "-f": "overwrites without asking",
       "--force": "overwrites without asking"}, operands="move", min_operands=1)
_ZIP_FORBIDDEN = {"-c": "writes binary data to the output", "--stdout": "writes binary data to the output",
                  "-f": "overwrites files and follows links", "--force": "overwrites files and follows links"}
_rule("gzip", "write", "compress files",
      "-k -v -d -n -N -1 -2 -3 -4 -5 -6 -7 -8 -9 --keep --verbose --decompress --best --fast", {},
      dict(_ZIP_FORBIDDEN), operands="write", min_operands=1)
_rule("gunzip", "write", "decompress files", "-k -v -n -N --keep --verbose", {}, dict(_ZIP_FORBIDDEN),
      operands="write", min_operands=1)
_rule("tar", "write", "create, list or extract archives (-t is read only)", operands="tar")
_rule("chmod", "write", "change permissions (no special bits)",
      "-R -v -c -f --recursive --verbose --changes --silent --quiet", {"--reference": "path_read"},
      operands="mode+write", min_operands=1)

# ---------------------------------------------------------------- delete (write level + approval every time)

_rule("rm", "delete", "delete files (asks the user every time)",
      "-r -R -f -v -d --recursive --force --verbose --dir", {},
      {"--no-preserve-root": "removes the protection of /", "-i": INTERACTIVE, "-I": INTERACTIVE,
       "--interactive": INTERACTIVE}, operands="delete", min_operands=1, approval="always")

# ---------------------------------------------------------------- never run, with the reason

NEVER: dict[str, str] = {}
for _names, _reason in (
    ("sudo su doas pkexec", "changes user or gains administrator rights"),
    ("bash sh zsh dash ksh csh tcsh fish busybox", "is a shell; Champion runs one command without a shell"),
    ("python python3 perl ruby node php lua tclsh Rscript",
     "is an interpreter; only trusted, hash-pinned scripts may run one (step 4c)"),
    ("env xargs nohup nice ionice timeout setsid stdbuf chroot unshare nsenter flock watch",
     "runs another command (the policy would not see it)"),
    ("ssh scp sftp rsync curl wget nc ncat netcat telnet ftp socat", "uses the network"),
    ("dd mount umount losetup mkfs fdisk parted", "works on disks and devices"),
    ("chown chgrp", "changes file owners"),
    ("crontab at batch systemctl service", "schedules or controls services"),
    ("ln link", "creates links (links can point outside the allowed area)"),
    ("awk gawk mawk sed", "is a programming language (it can run commands and write files)"),
    ("strace ltrace gdb", "inspects other processes"),
    ("kill pkill killall", "controls processes (process control is not part of step 4)"),
    ("tee truncate shred unlink rmdir", "changes or deletes files outside the listed write and delete commands"),
    ("squeue sinfo sbatch scancel srun salloc sacct qsub qstat qdel pbsnodes", HPC),
):
    for _name in _names.split():
        NEVER[_name] = _reason


# ---------------------------------------------------------------- find and tar grammar

FIND_VALUES = {"-name": "text", "-iname": "text", "-path": "text", "-ipath": "text", "-wholename": "text",
               "-type": "text", "-size": "text", "-mtime": "text", "-mmin": "text", "-atime": "text",
               "-amin": "text", "-ctime": "text", "-cmin": "text", "-newer": "path_read", "-perm": "text",
               "-user": "text", "-group": "text", "-links": "text", "-maxdepth": "int", "-mindepth": "int",
               "-regex": "text", "-iregex": "text", "-printf": "text"}
FIND_FLAGS = {"-empty", "-print", "-print0", "-prune", "-a", "-o", "-and", "-or", "-not", "!", "(", ")", "-ls",
              "-depth", "-xdev", "-mount", "-true", "-false", "-readable", "-writable", "-executable", "-nouser",
              "-nogroup"}
FIND_FORBIDDEN = {"-exec": RUNS_PROGRAM, "-execdir": RUNS_PROGRAM, "-ok": RUNS_PROGRAM, "-okdir": RUNS_PROGRAM,
                  "-delete": "deletes files (rm asks for approval)", "-fprint": "writes files",
                  "-fprint0": "writes files", "-fprintf": "writes files", "-fls": "writes files",
                  "-L": FOLLOW, "-H": FOLLOW, "-follow": FOLLOW, "-files0-from": FILES_FROM}

TAR_RULE = CommandRule(
    "tar", "write", "archives",
    frozenset("-c -x -t -z -j -J -v -k --create --extract --list --gzip --bzip2 --xz --verbose --keep-old-files "
              "--skip-old-files --no-same-owner --no-same-permissions".split()),
    {"-f": "text", "--file": "text", "-C": "text", "--directory": "text", "--exclude": "text"},
    {"--to-command": RUNS_PROGRAM, "--checkpoint-action": RUNS_PROGRAM, "--use-compress-program": RUNS_PROGRAM,
     "-I": RUNS_PROGRAM, "--info-script": RUNS_PROGRAM, "-F": RUNS_PROGRAM, "--new-volume-script": RUNS_PROGRAM,
     "--rsh-command": RUNS_PROGRAM, "--rmt-command": RUNS_PROGRAM, "-O": "prints archive contents",
     "--to-stdout": "prints archive contents", "-P": "keeps absolute paths (can write anywhere)",
     "--absolute-names": "keeps absolute paths (can write anywhere)", "-h": FOLLOW, "--dereference": FOLLOW,
     "--overwrite": "overwrites existing files", "-U": "deletes files first", "--unlink-first": "deletes files first",
     "--recursive-unlink": "deletes folders first", "--remove-files": "deletes the files it archives",
     "-T": FILES_FROM, "--files-from": FILES_FROM, "-X": FILES_FROM, "--exclude-from": FILES_FROM,
     "-p": "keeps special permission bits", "--preserve-permissions": "keeps special permission bits",
     "--same-permissions": "keeps special permission bits", "-r": "appends (not supported)",
     "-u": "appends (not supported)", "-A": "appends (not supported)", "-M": "multi-volume (not supported)"},
    operands="tar")

PS_FIELDS = {"pid", "ppid", "pgid", "sid", "user", "euser", "ruser", "uid", "comm", "ucomm", "%cpu", "pcpu",
             "%mem", "pmem", "rss", "rssize", "vsz", "sz", "etime", "etimes", "time", "cputime", "stat", "state",
             "s", "nlwp", "psr", "ni", "nice", "pri", "lstart", "start", "start_time", "tty"}


# ---------------------------------------------------------------- checking

@dataclass
class CommandCheck:
    """The decision for one command: final argv (wildcards expanded) and the checked paths."""
    command: str
    argv: list[str]
    rule: CommandRule
    command_class: str
    cwd: Path
    reads: list[Path] = field(default_factory=list)
    writes: list[Path] = field(default_factory=list)
    deletes: list[Path] = field(default_factory=list)

    @property
    def needs_approval(self) -> bool:
        return self.command_class in ("delete", "process_control") or self.rule.approval == "always"

    @property
    def affected(self) -> list[str]:
        return [str(p) for p in self.writes + self.deletes]


def _deny(message: str) -> None:
    raise PolicyDenied(message)


def _check_mode(value: str) -> str:
    if re.fullmatch(r"[0-7]{1,4}", value):
        if int(value, 8) & 0o7000:
            _deny(f"mode {value} sets special bits (setuid, setgid or sticky); not allowed")
        return value
    if re.fullmatch(r"[ugoa]*[-+=][rwxX]*(,[ugoa]*[-+=][rwxX]*)*", value):
        return value
    _deny(f"mode {value!r} is not allowed (use octal like 644 or symbolic like u+rw,go-w; no s or t bits)")


def _check_ps(value: str, sort: bool) -> str:
    for name in re.split(r"[,\s]+", value.strip()):
        word = name.lstrip("+-") if sort else name
        if word not in PS_FIELDS:
            _deny(f"ps field {name!r} is not allowed (allowed: {', '.join(sorted(PS_FIELDS))}; "
                  "args/cmd/command are never shown because they can contain secrets)")
    return value


def _check_value(rule: CommandRule, option: str, kind: str, value: str) -> str:
    if kind == "int" and not re.fullmatch(r"\d{1,9}", value):
        _deny(f"{rule.command} {option} needs a whole number (got {value!r})")
    if kind == "count" and not re.fullmatch(r"[+-]?\d{1,12}[a-zA-Z]{0,3}", value):
        _deny(f"{rule.command} {option} needs a number such as 20 (got {value!r})")
    if kind == "mode":
        _check_mode(value)
    if kind == "ps_format":
        _check_ps(value, sort=False)
    if kind == "ps_sort":
        _check_ps(value, sort=True)
    return value


def _unknown(rule: CommandRule, option: str) -> None:
    allowed = sorted(rule.flags | set(rule.values))
    _deny(f"option {option} of {rule.command} is not in the allowed list"
          + (f" (allowed: {' '.join(allowed)})" if allowed else " (it takes no options)"))


def _parse(rule: CommandRule, tokens: list[str]) -> tuple[list[tuple[str, str | None]], list[int]]:
    """GNU-style parse: (options with values, indexes of operand tokens)."""
    options: list[tuple[str, str | None]] = []
    operands: list[int] = []
    i, only_operands = 0, False
    while i < len(tokens):
        token, index = tokens[i], i
        i += 1
        if only_operands or token == "-" or not token.startswith("-"):
            operands.append(index)
            continue
        if token == "--":
            only_operands = True
            continue
        if token.startswith("--"):
            name, eq, value = token.partition("=")
            if name in rule.forbidden:
                _deny(f"{rule.command} {name} is not allowed: it {rule.forbidden[name]}")
            if name in rule.flags and not eq:
                options.append((name, None))
            elif name in rule.values:
                if not eq:
                    if i >= len(tokens):
                        _deny(f"{rule.command} {name} needs a value")
                    value, i = tokens[i], i + 1
                options.append((name, _check_value(rule, name, rule.values[name], value)))
            else:
                _unknown(rule, name)
            continue
        if token in rule.forbidden:  # multi-letter short forms such as -ww
            _deny(f"{rule.command} {token} is not allowed: it {rule.forbidden[token]}")
        j = 1
        while j < len(token):
            name = "-" + token[j]
            if name in rule.forbidden:
                _deny(f"{rule.command} {name} is not allowed: it {rule.forbidden[name]}")
            if name in rule.flags:
                options.append((name, None))
                j += 1
                continue
            if name in rule.values:
                value = token[j + 1:]
                if not value:
                    if i >= len(tokens):
                        _deny(f"{rule.command} {name} needs a value")
                    value, i = tokens[i], i + 1
                options.append((name, _check_value(rule, name, rule.values[name], value)))
                break
            _unknown(rule, name)
    return options, operands


def _parse_find(tokens: list[str]) -> tuple[list[int], list[str]]:
    """(indexes of start paths, -newer reference paths)."""
    i = 0
    while i < len(tokens) and tokens[i] in ("-P", "-L", "-H", "-D", "-O"):
        if tokens[i] != "-P":
            _deny(f"find {tokens[i]} is not allowed: it " + FIND_FORBIDDEN.get(tokens[i], "is a debug option"))
        i += 1
    starts = []
    while i < len(tokens) and not (tokens[i].startswith("-") or tokens[i] in ("(", ")", "!")):
        starts.append(i)
        i += 1
    references = []
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token in FIND_FORBIDDEN:
            _deny(f"find {token} is not allowed: it {FIND_FORBIDDEN[token]}")
        if token in FIND_FLAGS:
            continue
        if token in FIND_VALUES:
            if i >= len(tokens):
                _deny(f"find {token} needs a value")
            value = tokens[i]
            i += 1
            if FIND_VALUES[token] == "int" and not re.fullmatch(r"\d{1,4}", value):
                _deny(f"find {token} needs a whole number")
            if FIND_VALUES[token] == "path_read":
                references.append(value)
            continue
        if not token.startswith("-"):
            _deny(f"find: {token!r} must come before the tests (start folders first, then -name ...)")
        allowed = " ".join(sorted(set(FIND_VALUES) | FIND_FLAGS))
        _deny(f"find {token} is not in the allowed list (allowed: {allowed})")
    return starts, references


def _expand(raw: str, cwd: Path, level: AccessLevel, work: Path) -> list[str]:
    """Expand a wildcard in the last path part, like a shell, but inside checked folders."""
    if not GLOB.search(raw):
        return [raw]
    head, tail = os.path.split(raw)
    if GLOB.search(head):
        _deny(f"{raw!r}: wildcards are allowed only in the last part of a path")
    folder = check_path(level, work, _absolute(head or ".", cwd), "read")
    try:
        entries = os.listdir(folder)
    except OSError as exc:
        _deny(f"cannot list {folder} to expand {raw!r} ({exc.strerror})")
    names = sorted(n for n in entries if fnmatch.fnmatchcase(n, tail) and not name_reason(n)
                   and (tail.startswith(".") or not n.startswith(".")))
    if not names:
        _deny(f"no files match {raw!r}")
    if len(names) > MAX_GLOB:
        _deny(f"{raw!r} matches {len(names)} entries (more than {MAX_GLOB}); narrow the pattern")
    result = [os.path.join(head, n) if head else n for n in names]
    return [f"./{r}" if r.startswith("-") else r for r in result]


def _absolute(raw: str, cwd: Path) -> str:
    return raw if os.path.isabs(raw) else str(cwd / raw)


def check_command(argv: list[str], level: AccessLevel, work: Path, cwd: Path | None = None) -> CommandCheck:
    """Decide one command. Raises ``PolicyDenied`` with a clear reason, or returns the check."""
    cwd = cwd or work
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        _deny("a command is a non-empty list of strings, for example [\"ls\", \"-l\"]")
    if len(argv) > MAX_ARGS or any(len(a) > MAX_ARG_CHARS for a in argv):
        _deny(f"command too long (at most {MAX_ARGS} arguments of {MAX_ARG_CHARS} characters)")
    if any("\x00" in a for a in argv):
        _deny("arguments must not contain NUL characters")
    command = argv[0]
    if "/" in command or not command:
        _deny(f"give the command by name (for example 'ls'), not as a path: {command!r}")
    if command in NEVER:
        _deny(f"'{command}' is never run by Champion: it {NEVER[command]}")
    rule = RULES.get(command)
    if rule is None:
        _deny(f"'{command}' is not in the allowed command list (allowed: {', '.join(sorted(RULES))})")
    tokens = argv[1:]
    command_class = rule.command_class
    reads: list[str] = []
    writes: list[str] = []
    deletes: list[str] = []
    path_operands: set[int] = set()

    if rule.operands == "find":
        starts, references = _parse_find(tokens)
        path_operands.update(starts)
        reads += references
        operands = [tokens[i] for i in starts]
        read_ops = operands or ["."]
        options: list[tuple[str, str | None]] = []
    else:
        active = TAR_RULE if rule.operands == "tar" else rule
        if rule.operands == "tar" and tokens and not tokens[0].startswith("-"):
            _deny("write tar options with a dash (tar -czf out.tar.gz folder), not the old 'tar czf' form")
        options, operand_index = _parse(active, tokens)
        operands = [tokens[i] for i in operand_index]
        given = {name for name, _ in options}
        if len(operands) < rule.min_operands and not (rule.operands in ("copy", "move", "mode+write")):
            _deny(f"{command} needs at least {rule.min_operands} file argument(s)")
        if rule.max_operands is not None and len(operands) > rule.max_operands:
            _deny(f"{command} takes at most {rule.max_operands} file argument(s)")
        for name, value in options:
            kind = active.values.get(name)
            if kind == "path_read":
                reads.append(value)
            elif kind == "path_write":
                writes.append(value)
        read_ops: list[str] = []
        mode = rule.operands
        if mode == "read":
            read_ops = operands or (["."] if command in ("ls", "du", "df") else [])
            path_operands.update(operand_index)
        elif mode == "none":
            if operands:
                _deny(f"{command} takes no file arguments (got {operands[0]!r})")
        elif mode == "write":
            writes += operands
            path_operands.update(operand_index)
        elif mode == "pattern+read":
            if given & {"-e", "--regexp", "-f", "--file"}:
                read_ops = operands
                path_operands.update(operand_index)
            else:
                if not operands:
                    _deny("grep needs a pattern")
                read_ops = operands[1:]
                path_operands.update(operand_index[1:])
        elif mode in ("copy", "move"):
            target = given & {"-t", "--target-directory"}
            needed = 1 if target else 2
            if len(operands) < needed:
                _deny(f"{command} needs a source and a destination")
            path_operands.update(operand_index)
            if mode == "copy":
                read_ops = operands if target else operands[:-1]
                writes += [] if target else operands[-1:]
            else:
                writes += operands
        elif mode == "delete":
            deletes += operands
            path_operands.update(operand_index)
        elif mode == "mode+write":
            if "--reference" in given:
                writes += operands
                path_operands.update(operand_index)
            else:
                if len(operands) < 2:
                    _deny("chmod needs a mode and at least one file")
                _check_mode(operands[0])
                writes += operands[1:]
                path_operands.update(operand_index[1:])
        elif mode == "tar":
            command_class = _tar_paths(options, operands, operand_index, reads, writes, path_operands)

        reads += read_ops if mode != "tar" else []

    # wildcards in path operands, expanded at their place in the argument list
    final: list[str] = [command]
    expanded: dict[str, list[str]] = {}
    for index, token in enumerate(tokens):
        if index in path_operands and GLOB.search(token):
            expanded[token] = _expand(token, cwd, level, work)
            final += expanded[token]
        else:
            final.append(token)

    def grow(items: list[str]) -> list[str]:
        out = []
        for item in items:
            out += expanded.get(item, [item])
        return out

    if rule.operands == "find":
        reads = read_ops + reads
    reads, writes, deletes = grow(reads), grow(writes), grow(deletes)

    if command_class == "read" and not level.allows("read"):
        _deny(f"access level {level.name} ({level.label}) does not allow reading")
    if command_class in ("write", "delete") and not level.allows("write"):
        _deny(f"access level {level.name} ({level.label}) does not allow {command_class} commands "
              f"('{command}' changes files); the user can switch with /level 1.2")
    check = CommandCheck(command, final, rule, command_class, cwd)
    check.reads = [check_path(level, work, _absolute(p, cwd), "read") for p in reads]
    check.writes = [check_path(level, work, _absolute(p, cwd), "write") for p in writes]
    for raw in deletes:
        path = check_path(level, work, _absolute(raw, cwd), "write")
        if path == work or path in work.parents or path == cwd or path in cwd.parents:
            _deny(f"{path} is the working folder, the current folder or one of their parents; never deleted")
        check.deletes.append(path)
    return check


def _tar_paths(options, operands, operand_index, reads, writes, path_operands) -> str:
    given = {name for name, _ in options}
    values = {name: value for name, value in options if value is not None}
    modes = [m for m in (("-c", "--create"), ("-x", "--extract"), ("-t", "--list")) if given & set(m)]
    if len(modes) != 1:
        _deny("tar needs exactly one of -c (create), -x (extract) or -t (list)")
    archive = values.get("-f") or values.get("--file")
    if not archive:
        _deny("tar needs -f ARCHIVE (Champion never uses a tape or the input)")
    if ":" in archive:
        _deny("tar archive names with ':' mean a remote machine; not allowed")
    folder = values.get("-C") or values.get("--directory")
    mode = modes[0][0]
    if mode == "-t":
        reads.append(archive)
        return "read"
    if mode == "-c":
        if not operands:
            _deny("tar -c needs the files or folders to put in the archive")
        writes.append(archive)
        base = folder or "."
        if folder:
            reads.append(folder)
        reads += [os.path.join(base, o) for o in operands]  # checked; argv keeps the original names
        return "write"
    if not given & {"-k", "--keep-old-files", "--skip-old-files"}:
        _deny("tar -x needs -k (keep old files) or --skip-old-files, so existing files are never overwritten")
    reads.append(archive)
    writes.append(folder or ".")
    return "write"


def describe_rules() -> str:
    """Short overview for the agent's instructions and the docs."""
    by_class: dict[str, list[str]] = {}
    for rule in RULES.values():
        by_class.setdefault(rule.command_class, []).append(rule.command)
    return "; ".join(f"{cls}: {' '.join(sorted(cmds))}" for cls, cmds in by_class.items())
