"""``/access`` in the chat and ``champion access`` on the command line (step 4e).

Both only change the access list on behalf of the user; no agent tool calls
this module. Every change in a chat is one audit row (``ChampionContext.set_access``).

Chat commands (changes are for this session until ``/access save``)::

    /access                         show the list
    /access add PATH read|edit|none add a folder (or change the right of a listed one)
    /access set N read|edit|none    change folder N
    /access remove N                remove folder N
    /access outside none|read|edit  everything else outside the listed folders
    /access working read|edit       the working folder
    /access preset 1.1|1.2|2.1|2.2  working folder and everything else from a preset (folders kept)
    /access save                    keep the list for the next start (access.file)

Edit access to a very wide folder (``/``, the home folder, a parent of the
working folder) asks ``Continue? [y/N]`` first; default no.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Callable

import yaml

from champion.policy.access import (PRESETS, AccessError, AccessLevel, build_access, check_folder, is_wide,
                                    load_saved, norm_right, save)

USAGE = """Access commands (changes are for this session until /access save):
  /access                         show the access list
  /access add PATH read|edit|none add a folder with its right
  /access set N read|edit|none    change the right of folder N
  /access remove N                remove folder N
  /access outside none|read|edit  everything else outside the listed folders
  /access working read|edit       the working folder
  /access preset 1.1|1.2|2.1|2.2  start again from a preset (your folders are kept)
  /access save                    keep this list for the next start"""

Confirm = Callable[[str], bool]


def saved_text(level: AccessLevel) -> str:
    data = {"working": level.working, "outside": level.outside,
            "folders": [{"path": str(f.path), "right": f.right} for f in level.folders]}
    return yaml.safe_dump(data, sort_keys=False)


def show(level: AccessLevel, working_directory: Path, note: str = "this session") -> str:
    return f"Access {level.short()} ({note})\n{level.table(working_directory)}"


def _split_path_right(argument: str) -> tuple[str, str]:
    path, _, right = argument.strip().rpartition(" ")
    if not path.strip() or not right:
        raise AccessError("use /access add PATH read|edit|none")
    return path.strip(), norm_right(right)


def _number(text: str) -> int:
    text = text.strip().lstrip("#")
    if not text.isdigit():
        raise AccessError("use the folder number shown by /access")
    return int(text)


def _wide_ok(path: Path, right: str, working_directory: Path, confirm: Confirm | None) -> bool:
    if right != "edit" or not is_wide(path, working_directory):
        return True
    if confirm is None:
        return False
    return confirm(f"This allows editing in all of {path}. Continue? [y/N] ")


def change(level: AccessLevel, working_directory: Path, argument: str,
           confirm: Confirm | None = None) -> tuple[AccessLevel, str]:
    """Apply one ``/access`` sub-command; returns the new list and a short message.

    Raises ``AccessError`` for anything that is not valid; the list is then unchanged.
    """
    command, _, rest = argument.strip().partition(" ")
    command = command.lower()
    if command == "add":
        path_text, right = _split_path_right(rest)
        path = check_folder(path_text, working_directory, right)
        if not _wide_ok(path, right, working_directory, confirm):
            raise AccessError(f"not changed: edit access to all of {path} was not confirmed")
        return level.with_folder(path, right), f"Folder {path} added with {right}"
    if command == "set":
        number_text, _, right = rest.strip().partition(" ")
        number, right = _number(number_text), norm_right(right)
        folder = level.folder(number)
        path = check_folder(str(folder.path), working_directory, right)
        if not _wide_ok(path, right, working_directory, confirm):
            raise AccessError(f"not changed: edit access to all of {path} was not confirmed")
        return level.with_folder_right(number, right), f"Folder {number} ({path}) is now {right}"
    if command == "remove":
        number = _number(rest)
        folder = level.folder(number)
        return level.without_folder(number), f"Folder {number} ({folder.path}) removed"
    if command == "outside":
        right = norm_right(rest)
        if right == "edit" and not (confirm and confirm("This allows editing everywhere outside the listed "
                                                        "folders (blocked folders excepted). Continue? [y/N] ")):
            raise AccessError("not changed: edit access to everything else was not confirmed")
        return level.with_rights(outside=right), f"Everything else is now {right}"
    if command == "working":
        right = norm_right(rest)
        if right == "none":
            raise AccessError("the working folder is read or edit; use another working folder to exclude it")
        return level.with_rights(working=right), f"Working folder is now {right}"
    if command == "preset":
        name = rest.strip().lower()
        if name not in PRESETS:
            raise AccessError(f"unknown preset {name!r}; choose one of {', '.join(PRESETS)}")
        return level.with_preset(name), f"Preset {name} applied (folders kept)"
    raise AccessError(f"unknown /access command {command!r}\n{USAGE}")


def save_with_diff(level: AccessLevel, access_file: Path | None) -> str:
    """Write the list to ``access_file`` and return what changed in the saved file."""
    if access_file is None:
        raise AccessError("saving is off (access.file is null in config.yaml)")
    access_file = Path(access_file)
    try:
        old_data = load_saved(access_file)
    except AccessError:
        old_data = None
    old = yaml.safe_dump(old_data, sort_keys=False) if old_data else ""
    new = saved_text(level)
    diff = [line.rstrip("\n") for line in difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), "saved", "new", n=0)]
    save(level, access_file)
    body = "\n".join(diff) if diff else "(no change)"
    return f"Saved to {access_file}:\n{body}"


def chat_access(context, argument: str, confirm: Confirm | None = None) -> str:
    """The text for one ``/access ...`` line in the chat."""
    argument = argument.strip()
    if not argument:
        text = show(context.level, context.working_directory)
        if context.access_warnings:
            text += "\n" + "\n".join(f"Warning: {w}" for w in context.access_warnings)
        return text
    if argument.lower() in ("help", "?"):
        return USAGE
    try:
        if argument.lower() == "save":
            return save_with_diff(context.level, context.access_file)
        level, message = change(context.level, context.working_directory, argument, confirm)
    except (AccessError, OSError) as exc:
        return f"Access not changed: {exc}"
    context.set_access(level, message, {"command": argument})
    return f"{message}.\n{show(level, context.working_directory)}"


def cli_access(config, action: str, values: list[str], access_file: Path | None,
               confirm: Confirm | None = None) -> tuple[int, str]:
    """``champion access list|add|remove|outside|working`` on the saved list; (exit code, text)."""
    import os

    work = Path(os.path.realpath(config.get("execution.working_directory")))
    try:
        level, warnings = build_access(config, saved_file=access_file)
        if action == "list":
            note = f"saved in {access_file}" if access_file else "config only; saving is off"
            text = show(level, work, note)
            return 0, text + "".join(f"\nWarning: {w}" for w in warnings)
        if action == "remove" and values and not values[0].lstrip("#").isdigit():
            target = check_folder(values[0], work, "none")
            numbers = [i for i, f in enumerate(level.folders, 1) if f.path == target]
            if not numbers:
                raise AccessError(f"{target} is not in the list")
            values = [str(numbers[0])]
        argument = " ".join([action, *values])
        level, message = change(level, work, argument, confirm)
        return 0, save_with_diff(level, access_file) + f"\n{message}."
    except (AccessError, OSError) as exc:
        return 2, f"Access not changed: {exc}"
