"""Tests for per-folder access (step 4e): read or edit per folder, plus everything else.

Note: pytest folder names come from test names, so names avoid words that
sensitive.py blocks.
"""

import os
import stat
from pathlib import Path

import pytest

from champion import cli
from champion.access_commands import chat_access
from champion.chat import HELP
from champion.core.config import ConfigError, load_config
from champion.core.context import ChampionContext
from champion.policy.access import BUILT_IN, AccessError, build_access, check_folder, make
from champion.policy.scope import PolicyDenied, check_path
from champion.policy.tool_policy import all_rules
from champion.storage.audit import AuditLog
from champion.tools.edit import edit_file
from champion.tools.shell import run_shell


@pytest.fixture
def env(tmp_path):
    work = (tmp_path / "work").resolve()
    models = (tmp_path / "models").resolve()
    runs = (tmp_path / "runs").resolve()
    for folder in (work, models, runs, work / "reference"):
        folder.mkdir(parents=True)
    (work / "main.k").write_text("*KEYWORD\n*END\n")
    (work / "reference" / "ref.k").write_text("*KEYWORD\n*END\n")
    (models / "car.k").write_text("*KEYWORD\n*END\n")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.k").write_text("*END\n")
    return tmp_path, work, models, runs


def config_for(tmp_path, work, extra=""):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text(
        f"llm:\n  model_id: m\nexecution:\n  working_directory: '{work}'\n" + extra, encoding="utf-8")
    return load_config(folder / "config.yaml", env={}, cwd=tmp_path)


def allowed(level, work, path, right):
    try:
        check_path(level, work, str(path), right)
        return True
    except PolicyDenied:
        return False


# ------------------------------------------------------------- presets

@pytest.mark.parametrize("name,in_r,in_e,out_r,out_e", [
    ("1.1", True, False, False, False), ("1.2", True, True, False, False),
    ("2.1", True, False, True, False), ("2.2", True, True, True, True)])
def test_presets_match_the_old_levels(env, name, in_r, in_e, out_r, out_e):
    tmp_path, work, models, _ = env
    level = BUILT_IN[name]
    assert allowed(level, work, work / "main.k", "read") is in_r
    assert allowed(level, work, work / "main.k", "edit") is in_e
    assert allowed(level, work, models / "car.k", "read") is out_r
    assert allowed(level, work, models / "car.k", "edit") is out_e
    assert allowed(level, work, models / "car.k", "write") is out_e  # old name of edit


# ------------------------------------------------------------- the list

def test_folders_widen_narrow_and_block(env):
    tmp_path, work, models, runs = env
    level = (BUILT_IN["1.2"].with_folder(models, "read").with_folder(runs, "edit")
             .with_folder(work / "reference", "read"))
    assert allowed(level, work, models / "car.k", "read") and not allowed(level, work, models / "car.k", "edit")
    assert allowed(level, work, runs / "new.k", "edit")
    assert allowed(level, work, work / "main.k", "edit")
    assert allowed(level, work, work / "reference" / "ref.k", "read")
    with pytest.raises(PolicyDenied, match=r"read only \(folder 3.*/access set 3 edit"):
        check_path(level, work, str(work / "reference" / "ref.k"), "edit")
    with pytest.raises(PolicyDenied, match="/access add PATH read"):
        check_path(level, work, str(tmp_path / "elsewhere" / "x.k"), "read")
    # widen inside a read folder; none blocks inside a readable outside
    (models / "scratch").mkdir()
    wide = BUILT_IN["2.1"].with_folder(models, "read").with_folder(models / "scratch", "edit")
    assert allowed(wide, work, models / "scratch" / "a.k", "edit") and not allowed(wide, work, models / "b.k", "edit")
    blocked = BUILT_IN["2.1"].with_folder(models, "none")
    assert allowed(blocked, work, tmp_path / "elsewhere" / "x.k", "read")
    with pytest.raises(PolicyDenied, match=r"blocked \(folder 1"):
        check_path(blocked, work, str(models / "car.k"), "read")


def test_many_folders_and_longest_match(env):
    tmp_path, work, _, _ = env
    level = BUILT_IN["1.1"]
    for i in range(50):
        folder = tmp_path / "many" / f"f{i}"
        folder.mkdir(parents=True)
        level = level.with_folder(check_folder(str(folder), work, "read"), "edit" if i % 2 else "read")
    assert len(level.folders) == 50 and level.short() == "1.1 + 50 folders"
    assert allowed(level, work, tmp_path / "many" / "f7" / "a.k", "edit")
    assert not allowed(level, work, tmp_path / "many" / "f8" / "a.k", "edit")
    assert level.with_folder(tmp_path / "many" / "f8", "edit").folders[8].right == "edit"  # changed, not added


def test_adding_folders_is_checked(env):
    tmp_path, work, models, _ = env
    with pytest.raises(AccessError, match="not an existing folder"):
        check_folder(str(tmp_path / "missing"), work, "read")
    (tmp_path / "linked").symlink_to(models)
    with pytest.raises(AccessError, match="symbolic link"):
        check_folder(str(tmp_path / "linked"), work, "read")
    (tmp_path / ".ssh").mkdir()
    with pytest.raises(AccessError, match="secret"):
        check_folder(str(tmp_path / ".ssh"), work, "read")
    with pytest.raises(AccessError, match="system folder"):
        check_folder("/proc", work, "read")
    with pytest.raises(AccessError, match="never written"):
        check_folder("/etc", work, "edit")
    assert check_folder("../models", work, "read") == models  # relative to the working folder
    # a link inside a read folder pointing into an edit folder is refused, not followed
    level = BUILT_IN["1.1"].with_folder(models, "read").with_folder(env[3], "edit")
    (models / "to_runs").symlink_to(env[3])
    with pytest.raises(PolicyDenied, match="symbolic link"):
        check_path(level, work, str(models / "to_runs" / "a.k"), "edit")


def test_fixed_blocks_win_over_any_entry(env):
    tmp_path, work, _, _ = env
    level = BUILT_IN["2.2"]
    with pytest.raises(PolicyDenied, match="own folder"):
        check_path(level, work, "~/.champion/access.yaml", "edit")
    with pytest.raises(PolicyDenied, match="never written"):
        check_path(level, work, "/etc/hosts", "edit")


# ------------------------------------------------------------- tools obey the list

def test_tools_obey_folder_rights(env):
    tmp_path, work, models, runs = env
    config = config_for(tmp_path, work)
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog(), edit=(str(runs),),
                                          read=(str(models),))
    assert context.level.short() == "1.1 + 2 folders"
    with pytest.raises(PolicyDenied, match="read only \\(the working folder"):
        edit_file(context, "main.k", "*END", "*END\n")
    with pytest.raises(PolicyDenied, match="read only \\(folder 1"):
        run_shell(context, ["cp", str(models / "car.k"), str(models / "copy.k")])
    out = run_shell(context, ["cp", str(models / "car.k"), str(runs / "car.k")])
    assert (runs / "car.k").exists() and "in /changes" in out
    asked = []
    context.approver = lambda request: asked.append(request) or True
    run_shell(context, ["rm", str(runs / "car.k")])
    assert len(asked) == 1 and not (runs / "car.k").exists()  # delete in an edit folder still asks
    with pytest.raises(PolicyDenied):
        run_shell(context, ["rm", str(models / "car.k")])  # read folder: refused before any prompt
    assert len(asked) == 1 and (models / "car.k").exists()


# ------------------------------------------------------------- /access in the chat

def test_chat_access_commands_and_audit(env):
    tmp_path, work, models, runs = env
    config = config_for(tmp_path, work)
    audit = AuditLog()
    context = ChampionContext.from_config(config, level="2.1", audit=audit)
    out = chat_access(context, f"add {models} read")
    assert "Folder" in out and str(models) in out and context.level.short() == "2.1 + 1 folder"
    chat_access(context, f"add {runs} edit")
    assert "Folder 1" in chat_access(context, "set 1 edit") and context.level.folders[0].right == "edit"
    assert "removed" in chat_access(context, "remove 2") and len(context.level.folders) == 1
    assert "Everything else is now none" in chat_access(context, "outside none")
    assert "Access not changed" in chat_access(context, "working none")
    assert "Access not changed" in chat_access(context, "set 9 read")
    assert "Access not changed" in chat_access(context, f"add {tmp_path / 'missing'} read")
    assert "Preset 2.2 applied" in chat_access(context, "preset 2.2")
    assert context.level.name == "2.2" and len(context.level.folders) == 1  # folders kept
    context.set_level("1.1")
    assert context.level.name == "1.1" and len(context.level.folders) == 1  # /level keeps folders
    table = chat_access(context, "")
    assert "(working)" in table and "everything else" in table and str(models) in table
    rows = [r for r in audit.recent(50) if r["tool"] == "/access"]
    # one row per accepted change: add, add, set, remove, outside, preset, /level (refused ones: none)
    assert len(rows) == 7 and all(r["agent"] == "user" for r in rows)
    assert "saving is off" in chat_access(context, "save")  # no access file in tests


def test_wide_edit_asks_first(env):
    tmp_path, work, _, _ = env
    context = ChampionContext.from_config(config_for(tmp_path, work), level="1.1", audit=AuditLog())
    prompts = []
    out = chat_access(context, f"add {tmp_path} edit", lambda p: prompts.append(p) or False)
    assert "not confirmed" in out and not context.level.folders and "Continue? [y/N]" in prompts[0]
    assert "not confirmed" in chat_access(context, "outside edit")  # no prompt possible: refused
    chat_access(context, f"add {tmp_path} edit", lambda p: True)
    assert context.level.folders[0].path == tmp_path.resolve()
    chat_access(context, f"add {tmp_path} read")  # read access to a wide folder needs no question


def test_save_and_reload(env):
    tmp_path, work, models, runs = env
    config = config_for(tmp_path, work)
    saved = tmp_path / "state" / "access.yaml"
    context = ChampionContext.from_config(config, level="1.1", audit=AuditLog(), access_file=saved)
    chat_access(context, f"add {models} read")
    chat_access(context, f"add {runs} edit")
    assert not saved.exists()  # session only until /access save
    out = chat_access(context, "save")
    assert f"Saved to {saved}" in out and "+" in out
    assert stat.S_IMODE(os.stat(saved).st_mode) == 0o600
    again = ChampionContext.from_config(config, level=None, audit=AuditLog(), access_file=saved)
    assert [(f.path, f.right) for f in again.level.folders] == [(models, "read"), (runs, "edit")]
    os.chmod(saved, 0o666)
    level, warnings = build_access(config, saved_file=saved)
    assert not level.folders and "other users" in warnings[0]  # unsafe file not used
    os.chmod(saved, 0o600)
    runs.rename(tmp_path / "runs_moved")
    level, warnings = build_access(config, saved_file=saved)
    assert len(level.folders) == 1 and "skipped" in warnings[0]  # one moved folder does not stop the start


# ------------------------------------------------------------- config and CLI

def test_config_access_section_and_old_custom(env):
    tmp_path, work, models, runs = env
    config = config_for(tmp_path, work, f"access:\n  preset: 2.1\n  outside: none\n  folders:\n"
                                        f"    - {{path: '{models}', right: read}}\n"
                                        f"    - {{path: '{runs}', right: write}}\n")
    level, warnings = build_access(config)
    assert (level.working, level.outside, warnings) == ("read", "none", [])
    assert [(f.path, f.right) for f in level.folders] == [(models, "read"), (runs, "edit")]  # write -> edit
    with pytest.raises(ConfigError, match="access.folders entry 1"):
        config_for(tmp_path, work, "access:\n  folders:\n    - {path: '/x', right: everything}\n")
    with pytest.raises(ConfigError, match="access.outside"):
        config_for(tmp_path, work, "access:\n  outside: all\n")
    old = config_for(tmp_path, work, f"policy:\n  default_level: custom\n  custom:\n    read: outside\n"
                                     f"    write: working\n    extra_dirs: ['{runs}']\n")
    level, _ = build_access(old)
    assert (level.working, level.outside) == ("edit", "read") and level.folders[0].right == "edit"
    assert make("read", "read").name == "2.1" and make("edit", "read").name == "custom"


def test_cli_access_and_chat_flags(env, capsys):
    tmp_path, work, models, runs = env
    saved = tmp_path / "state" / "access.yaml"
    config_for(tmp_path, work, f"access:\n  file: '{saved}'\n")
    path = str(tmp_path / "config" / "config.yaml")
    assert cli.main(["--config", path, "access", "add", str(models), "read"]) == 0
    assert cli.main(["--config", path, "access", "list"]) == 0
    assert str(models) in capsys.readouterr().out
    assert cli.main(["--config", path, "access", "remove", str(models)]) == 0
    assert cli.main(["--config", path, "access", "add", str(tmp_path / "missing"), "read"]) == 2
    args = cli.build_parser().parse_args(["chat", "--read", str(models), "--edit", str(runs), "--outside", "none"])
    assert (args.read, args.edit, args.outside) == ([str(models)], [str(runs)], "none")


def test_no_access_tool_and_edit_wording(env):
    assert not [r.name for r in all_rules() if "access" in r.name.lower()]  # the model cannot change access
    assert "/access" in HELP and "edit" in HELP
    tmp_path, work, models, _ = env
    with pytest.raises(PolicyDenied) as blocked:
        check_path(BUILT_IN["2.1"], work, str(models / "car.k"), "edit")
    assert "edit" in str(blocked.value) and "writ" not in str(blocked.value)
