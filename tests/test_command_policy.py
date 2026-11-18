"""Tests for the command policy (step 4a): decisions only, nothing is run here."""

import pytest

from champion.core.config import load_config
from champion.execution.command_policy import NEVER, RULES, check_command
from champion.policy.levels import get_level
from champion.policy.scope import PolicyDenied


@pytest.fixture
def env(tmp_path):
    work = (tmp_path / "work").resolve()
    outside = (tmp_path / "outside").resolve()
    (work / "runs").mkdir(parents=True)
    outside.mkdir()
    for name in ("a.k", "b.k", "main.dyn", "-rf.k", ".hidden.k"):
        (work / name).write_text("*KEYWORD\n")
    (work / ".env").write_text("TOKEN=x\n")
    (work / "secret.k").write_text("x")
    (work / ".ssh").mkdir()
    (work / "link_out").symlink_to(outside)
    (outside / "far.k").write_text("x")
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "config.yaml").write_text(f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n")
    config = load_config(folder / "config.yaml", env={}, cwd=tmp_path)
    return config, work, outside


def check(env, argv, level="1.1", cwd=None):
    config, work, _ = env
    return check_command(argv, get_level(level, config), work, cwd)


def denied(env, argv, match, level="1.1"):
    with pytest.raises(PolicyDenied, match=match):
        check(env, argv, level)


def test_allowed_read_commands(env):
    _, work, _ = env
    result = check(env, ["ls", "-la"])
    assert result.command_class == "read" and result.reads == [work] and not result.needs_approval
    result = check(env, ["find", ".", "-name", "*.k", "-type", "f", "-maxdepth", "2"])
    assert result.argv == ["find", ".", "-name", "*.k", "-type", "f", "-maxdepth", "2"]  # -name value not expanded
    assert check(env, ["du", "-sh", "runs"]).reads == [work / "runs"]
    assert check(env, ["head", "-n", "-5", "a.k"]).reads == [work / "a.k"]
    assert check(env, ["ps", "-e", "-o", "pid,comm,%mem", "--sort=-rss"]).command_class == "read"
    assert check(env, ["tar", "-tzf", "x.tgz"]).command_class == "read"
    assert check(env, ["ls", "--", "-rf.k"]).reads == [work / "-rf.k"]  # after -- it is a file


def test_unknown_never_and_path_commands_blocked(env):
    denied(env, ["frobnicate"], "not in the allowed command list")
    denied(env, ["/bin/ls"], "by name")
    denied(env, ["./ls"], "by name")
    for command in ("bash", "sh", "zsh", "python", "python3", "perl", "sudo", "su", "env", "xargs", "nohup",
                    "nice", "ssh", "scp", "rsync", "curl", "wget", "nc", "dd", "mount", "chown", "crontab",
                    "systemctl", "ln", "awk", "sed", "timeout", "setsid", "stdbuf", "kill", "tee"):
        assert command in NEVER and command not in RULES
        denied(env, [command, "x"], "never run by Champion")
    for command in ("squeue", "sinfo", "sbatch", "scancel", "qsub", "qstat"):
        denied(env, [command], "HPC agent")
    for bad in ([], "ls -la", ["ls", 3], ["ls", "a\x00b"]):
        with pytest.raises(PolicyDenied):
            check(env, bad)


def test_unknown_and_dangerous_options_blocked(env):
    denied(env, ["ls", "--quoting-style=shell"], "not in the allowed list")
    denied(env, ["ls", "-L"], "follows symbolic links")
    denied(env, ["sort", "--compress=gzip", "a.k"], "not in the allowed list")  # no abbreviations
    denied(env, ["sort", "--compress-program=sh", "a.k"], "runs another program")
    denied(env, ["sort", "-o", "out", "a.k"], "writes a file")
    for option in ("-exec", "-execdir", "-ok", "-okdir"):
        denied(env, ["find", ".", option, "rm", "{}", ";"], "runs another program")
    denied(env, ["find", ".", "-delete"], "deletes files")
    denied(env, ["find", ".", "-fprint", "out"], "writes files")
    denied(env, ["find", "-L", "."], "follows symbolic links")
    denied(env, ["find", ".", "-name", "*.k", "extra"], "must come before the tests")
    denied(env, ["tar", "-xf", "x.tar", "--to-command=sh"], "runs another program")
    denied(env, ["tar", "-cf", "x.tar", "--checkpoint=1", "--checkpoint-action=exec=sh", "a.k"],
           "not in the allowed list|runs another program")
    denied(env, ["tar", "-c", "-I", "sh", "-f", "x.tar", "a.k"], "runs another program")
    denied(env, ["tar", "czf", "x.tgz", "a.k"], "with a dash")
    denied(env, ["tar", "-xf", "x.tar"], "needs -k", level="1.2")
    denied(env, ["tar", "-tf", "host:x.tar"], "remote")
    denied(env, ["grep", "-r", "MAT", "."], "filesystem_grep")
    denied(env, ["grep", "--include=*.k", "MAT", "a.k"], "filesystem_grep")
    denied(env, ["diff", "-r", "runs", "runs"], "filesystem_grep")
    denied(env, ["md5sum", "-c", "sums.md5"], "checksum file")
    denied(env, ["du", "--files0-from=list"], "list of file names")
    denied(env, ["tail", "-f", "a.k"], "forever")
    denied(env, ["ps", "-ef"], "command lines")
    denied(env, ["ps", "-e", "-o", "pid,args"], "never shown")
    denied(env, ["ps", "aux"], "takes no file arguments")
    denied(env, ["head", "-n", "many", "a.k"], "needs a number")
    denied(env, ["free", "-s", "1"], "repeats forever")


def test_paths_are_checked_like_the_file_tools(env):
    _, work, outside = env
    denied(env, ["head", str(outside / "far.k")], "outside the working folder")
    denied(env, ["head", "../outside/far.k"], "outside the working folder")
    denied(env, ["ls", "/etc"], "outside the working folder")
    assert check(env, ["ls", "/etc"], level="2.1").reads[0].as_posix() == "/etc"
    denied(env, ["head", "/etc/shadow"], "protected system file", level="2.2")
    denied(env, ["head", "link_out/far.k"], "symbolic link", level="2.2")
    denied(env, ["head", ".env"], "always blocked", level="2.2")
    denied(env, ["ls", ".ssh"], "always blocked", level="2.2")
    denied(env, ["ls", ".champion"], "own folder", level="2.2")
    denied(env, ["grep", "-f", "../outside/far.k", "a.k"], "outside the working folder")
    pattern = check(env, ["grep", "../../etc/passwd", "a.k"])  # the pattern is text, not a path
    assert pattern.reads == [work / "a.k"]
    denied(env, ["find", ".", "-newer", "../outside/far.k"], "outside the working folder")
    denied(env, ["tar", "-tf", "../outside/x.tar"], "outside the working folder")


def test_wildcards_are_expanded_by_champion(env):
    _, work, _ = env
    result = check(env, ["wc", "-l", "*.k"])
    assert result.argv == ["wc", "-l", "./-rf.k", "a.k", "b.k"]  # no .hidden.k, no secret.k, '-' guarded
    assert result.reads == [work / "-rf.k", work / "a.k", work / "b.k"]
    assert check(env, ["ls", ".*.k"]).argv == ["ls", ".hidden.k"]
    denied(env, ["ls", "*.nothing"], "no files match")
    denied(env, ["ls", "*/x.k"], "last part")
    denied(env, ["ls", "../outside/*.k"], "outside the working folder")


def test_write_and_delete_classes(env):
    _, work, outside = env
    denied(env, ["mkdir", "results"], "does not allow write commands")
    made = check(env, ["mkdir", "-p", "results/run1"], level="1.2")
    assert made.command_class == "write" and made.writes == [work / "results" / "run1"]
    assert not made.needs_approval
    copy = check(env, ["cp", "a.k", "b.k", "runs"], level="1.2")
    assert copy.reads == [work / "a.k", work / "b.k"] and copy.writes == [work / "runs"]
    assert check(env, ["cp", "-t", "runs", "a.k"], level="1.2").writes == [work / "runs"]
    denied(env, ["cp", "a.k", str(outside)], "outside the working folder", level="1.2")
    assert check(env, ["cp", "../outside/far.k", "runs"], level="2.2").reads == [outside / "far.k"]
    denied(env, ["cp", "a.k", "/usr/local/x"], "system folder|not writable|outside", level="2.2")
    denied(env, ["mv", "../outside/far.k", "runs"], "outside the working folder", level="1.2")
    denied(env, ["cp", "-s", "a.k", "l.k"], "symbolic links", level="1.2")
    assert check(env, ["chmod", "644", "a.k"], level="1.2").writes == [work / "a.k"]
    assert check(env, ["chmod", "-R", "u+rw,go-w", "runs"], level="1.2").writes == [work / "runs"]
    denied(env, ["chmod", "4755", "a.k"], "special bits", level="1.2")
    denied(env, ["chmod", "u+s", "a.k"], "not allowed", level="1.2")
    denied(env, ["mkdir", "-m", "2775", "x"], "special bits", level="1.2")
    archive = check(env, ["tar", "-czf", "out.tgz", "runs"], level="1.2")
    assert archive.writes == [work / "out.tgz"] and work / "runs" in archive.reads
    extract = check(env, ["tar", "-xkf", "out.tgz", "-C", "runs"], level="1.2")
    assert extract.writes == [work / "runs"] and extract.reads == [work / "out.tgz"]
    removed = check(env, ["rm", "-r", "runs"], level="1.2")
    assert removed.command_class == "delete" and removed.needs_approval and removed.deletes == [work / "runs"]
    denied(env, ["rm", "a.k"], "does not allow delete commands")
    denied(env, ["rm", "-rf", "."], "never deleted", level="1.2")
    denied(env, ["rm", "-rf", str(work.parent)], "outside|never deleted", level="2.2")
    denied(env, ["rm", "--no-preserve-root", "a.k"], "protection", level="1.2")
