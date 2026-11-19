"""Champion command line.

``champion --version``, ``champion doctor`` (step 0) and ``champion chat``
(steps 1-2a). Later steps add the terminal UI (fast demo step 6) and
``champion kb`` (L5, after the demo).
"""

from __future__ import annotations

import argparse
import sys

from champion import __version__
from champion.core.config import ACCESS_LEVELS, ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="champion", description="Champion CAE assistant")
    parser.add_argument("--version", action="version", version=f"champion {__version__}")
    parser.add_argument("--config", help="config file (default: CHAMPION_CONFIG or config/config.yaml)")
    commands = parser.add_subparsers(dest="command")

    doctor = commands.add_parser("doctor", help="check that Champion is ready to run")
    doctor.add_argument("--online", action="store_true",
                        help="also send one small request to each configured model")

    chat = commands.add_parser("chat", help="chat with Champion in the terminal")
    chat.add_argument("-q", "--question", help="ask one question, print the answer and exit")
    chat.add_argument("--level", choices=ACCESS_LEVELS,
                      help="access level for this chat (default: policy.default_level)")
    chat.add_argument("--plain", action="store_true",
                      help="print answers as raw text (default: Markdown in a terminal)")
    chat.add_argument("--read", action="append", default=[], metavar="FOLDER",
                      help="also allow reading FOLDER in this chat (repeatable)")
    chat.add_argument("--edit", action="append", default=[], metavar="FOLDER",
                      help="also allow editing FOLDER in this chat (repeatable)")
    chat.add_argument("--outside", choices=("none", "read", "edit"),
                      help="right for everything else outside the working folder and the listed folders")

    access = commands.add_parser("access", help="show or change the saved per-folder access list")
    access.add_argument("action", choices=("list", "add", "remove", "outside", "working"))
    access.add_argument("values", nargs="*", help="add PATH read|edit|none; remove PATH|N; "
                                                  "outside none|read|edit; working read|edit")
    return parser


def access_file(config):
    from pathlib import Path

    value = config.get("access.file")
    return Path(value) if value else None


def ask_yes(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        from champion.doctor import FAIL, render, run_checks

        results = run_checks(config, online=args.online)
        render(results)
        return 1 if any(r.status == FAIL for r in results) else 0

    if args.command == "access":
        from champion.access_commands import cli_access

        code, text = cli_access(config, args.action, args.values, access_file(config),
                                ask_yes if sys.stdin.isatty() else None)
        print(text, file=sys.stderr if code else sys.stdout)
        return code

    if args.command == "chat":
        from champion.chat import run_chat
        from champion.core.runtime import ChampionRuntime, RuntimeSetupError
        from champion.policy.access import AccessError

        try:
            runtime = ChampionRuntime(config, level=args.level, access_file=access_file(config),
                                      read=tuple(args.read), edit=tuple(args.edit), outside=args.outside)
        except AccessError as exc:
            print(f"Access error: {exc}", file=sys.stderr)
            return 2
        except RuntimeSetupError as exc:
            print(f"Cannot start: {exc}", file=sys.stderr)
            return 2
        return run_chat(runtime, args.question, plain=args.plain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
