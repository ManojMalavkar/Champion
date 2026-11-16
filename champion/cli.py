"""Champion command line.

``champion --version``, ``champion doctor`` (step 0) and ``champion chat``
(step 1). Later steps add the terminal UI (step 5) and ``champion kb`` (step 10).
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
                        help="also make one OpenAI API call to check the key and model")

    chat = commands.add_parser("chat", help="chat with Champion in the terminal")
    chat.add_argument("-q", "--question", help="ask one question, print the answer and exit")
    chat.add_argument("--level", choices=ACCESS_LEVELS,
                      help="access level for this chat (default: policy.default_level)")
    return parser


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

    if args.command == "chat":
        from champion.chat import run_chat
        from champion.core.runtime import ChampionRuntime, RuntimeSetupError

        try:
            runtime = ChampionRuntime(config, level=args.level)
        except RuntimeSetupError as exc:
            print(f"Cannot start: {exc}", file=sys.stderr)
            return 2
        return run_chat(runtime, args.question)
    return 0


if __name__ == "__main__":
    sys.exit(main())
