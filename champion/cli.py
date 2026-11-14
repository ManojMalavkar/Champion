"""Champion command line.

Step 0 provides ``champion --version`` and ``champion doctor``. Later steps add
the chat (step 1), the terminal UI (step 5) and ``champion kb`` (step 10).
"""

from __future__ import annotations

import argparse
import sys

from champion import __version__
from champion.core.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="champion", description="Champion CAE assistant")
    parser.add_argument("--version", action="version", version=f"champion {__version__}")
    parser.add_argument("--config", help="config file (default: CHAMPION_CONFIG or config/config.yaml)")
    commands = parser.add_subparsers(dest="command")

    doctor = commands.add_parser("doctor", help="check that Champion is ready to run")
    doctor.add_argument("--online", action="store_true",
                        help="also make one OpenAI API call to check the key and model")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
