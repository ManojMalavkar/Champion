"""Tests for access levels, blocked paths, path checks, tool policy and the audit log.

Note: pytest folder names come from test names, so test names here avoid words
that sensitive.py blocks (such as the word for hidden keys or passwords).
"""

import os
from pathlib import Path

import pytest

from champion.core.config import load_config
from champion.policy.levels import BUILT_IN, get_level
from champion.policy.scope import PolicyDenied, check_path, open_no_symlinks
from champion.policy.sensitive import sensitive_reason
from champion.policy.tool_policy import ToolRule, has_rule, register, rule_for
from champion.storage.audit import AuditLog


def config_for(tmp_path: Path, extra: str = ""):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: m\n" + extra, encoding="utf-8")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path)


@pytest.fixture
def dirs(tmp_path):
    work = (tmp_path / "work").resolve()
    other = (tmp_path / "other").resolve()
    work.mkdir()
    other.mkdir()
    (work / "a.k").write_text("*KEYWORD\n")
    (other / "b.k").write_text("*KEYWORD\n")
    return work, other


# ------------------------------------------------------------- levels

MATRIX = [  # level, inside read, inside write, outside read, outside write
    ("1.1", True, False, False, False),
    ("1.2", True, True, False, False),
    ("2.1", True, False, True, False),
    ("2.2", True, True, True, True),
]


@pytest.mark.parametrize("name,in_r,in_w,out_r,out_w", MATRIX)
def test_level_matrix(dirs, name, in_r, in_w, out_r, out_w):
    work, other = dirs
    level = BUILT_IN[name]
    for path, right, expected in [(work / "a.k", "read", in_r), (work / "a.k", "write", in_w),
                                  (other / "b.k", "read", out_r), (other / "b.k", "write", out_w)]:
        if expected:
            assert check_path(level, work, str(path), right) == path
        else:
            with pytest.raises(PolicyDenied):
                check_path(level, work, str(path), right)


def test_custom_level_with_extra_folder(tmp_path, dirs):
    work, other = dirs
    config = config_for(tmp_path, f"policy:\n  custom:\n    read: working\n    write: working\n"
                                  f"    extra_dirs: ['{other}']\n")
    level = get_level("custom", config)
    assert level.extra_dirs == (other,)
    assert check_path(level, work, str(other / "b.k"), "write") == other / "b.k"
    with pytest.raises(PolicyDenied, match="outside the working folder"):
        check_path(level, work, str(tmp_path / "elsewhere.k"), "read")


def test_unknown_level(tmp_path):
    with pytest.raises(ValueError, match="unknown access level"):
        get_level("3.0", config_for(tmp_path))


def test_relative_paths_and_dotdot(dirs):
    work, other = dirs
    level = BUILT_IN["1.1"]
    assert check_path(level, work, "a.k", "read") == work / "a.k"
    assert check_path(level, work, "./sub/../a.k", "read") == work / "a.k"
    with pytest.raises(PolicyDenied, match="outside the working folder"):
        check_path(level, work, "../other/b.k", "read")
    with pytest.raises(PolicyDenied, match="non-empty"):
        check_path(level, work, "  ", "read")


# ------------------------------------------------------------- always blocked

@pytest.mark.parametrize("path", [
    "/proc/self/environ", "/proc/1/cmdline", "/sys/kernel", "/dev/sda", "/root/notes.txt", "/etc/shadow",
    "/home/u/.ssh/config", "/home/u/.aws/config", "/home/u/project/.env", "/home/u/project/.env.local",
    "/home/u/prod.env", "/home/u/server.pem", "/home/u/api_token.txt", "/home/u/id_ed25519",
    "/home/u/.netrc", "/home/u/.databrickscfg", "/home/u/champion.db", "/home/u/data.sqlite3",
])
def test_fixed_blocks(path):
    assert sensitive_reason(path) is not None


@pytest.mark.parametrize("path", ["/home/u/model.k", "/home/u/main.key", "/home/u/run/d3plot", "/etc/hosts"])
def test_normal_paths_not_blocked(path):
    assert sensitive_reason(path) is None


def test_os_folders_never_written():
    assert sensitive_reason("/etc/hosts") is None
    assert "never written" in sensitive_reason("/etc/hosts", write=True)
    assert "never written" in sensitive_reason("/usr/local/bin/x", write=True)


def test_blocked_even_at_level_2_2(dirs):
    work, _ = dirs
    for path in ["/proc/self/environ", str(work / ".env"), str(work / ".ssh" / "config")]:
        with pytest.raises(PolicyDenied, match="always blocked"):
            check_path(BUILT_IN["2.2"], work, path, "read")


# ------------------------------------------------------------- symbolic links

def test_links_refused_anywhere_in_path(dirs):
    work, other = dirs
    (work / "to_other").symlink_to(other)
    (work / "b_link.k").symlink_to(other / "b.k")
    for path in ["to_other/b.k", "b_link.k", "to_other/../a.k"]:
        if path == "to_other/../a.k":
            # normalised lexically to work/a.k, which is not a link: allowed
            assert check_path(BUILT_IN["2.2"], work, path, "read") == work / "a.k"
            continue
        with pytest.raises(PolicyDenied, match="symbolic links"):
            check_path(BUILT_IN["2.2"], work, path, "read")


def test_open_no_symlinks_refuses_a_link_created_after_the_check(dirs):
    work, other = dirs
    path = check_path(BUILT_IN["1.1"], work, "later.k", "read")  # does not exist yet: allowed
    (work / "later.k").symlink_to(other / "b.k")                  # swapped in after the check
    with pytest.raises(OSError):
        open_no_symlinks(path)
    fd = open_no_symlinks(work / "a.k")
    try:
        assert os.read(fd, 8) == b"*KEYWORD"
    finally:
        os.close(fd)


# ------------------------------------------------------------- tool policy

def test_tool_policy_is_deny_by_default():
    with pytest.raises(PolicyDenied, match="no policy entry"):
        rule_for("not_a_registered_tool")
    rule = register(ToolRule("test_dummy_tool", "read", "dummy"))
    assert rule_for("test_dummy_tool") is rule and has_rule("test_dummy_tool")
    register(ToolRule("test_dummy_tool", "read", "dummy"))  # same entry again: fine
    with pytest.raises(ValueError, match="different policy entry"):
        register(ToolRule("test_dummy_tool", "write", "dummy"))
    with pytest.raises(ValueError):
        ToolRule("bad", "delete_everything", "x")


def test_builtin_tools_have_policy_entries():
    import champion.tools.filesystem  # noqa: F401  (registers its rules)
    import champion.tools.session_info  # noqa: F401

    assert rule_for("session_info").right == "none"
    for name in ("filesystem_list", "filesystem_read", "filesystem_disk_usage"):
        assert rule_for(name).right == "read"


# ------------------------------------------------------------- audit log

def test_audit_log_file_and_rows(tmp_path):
    path = tmp_path / "logs" / "audit.db"
    audit = AuditLog(path)
    audit.record(tool="filesystem_read", arguments={"path": "a.k"}, access_level="1.1",
                 decision="allow", result_chars=10, seconds=0.01, agent="Champion")
    audit.record(tool="filesystem_read", arguments={"path": "/etc/shadow"}, access_level="1.1",
                 decision="block", reason="protected system file")
    rows = audit.recent()
    assert [r["decision"] for r in rows] == ["allow", "block"]
    assert rows[1]["reason"] == "protected system file" and rows[0]["agent"] == "Champion"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    other_session = AuditLog(path)
    assert other_session.recent() == [] and len(other_session.recent(this_session=False)) == 2
    audit.close()
    other_session.close()
