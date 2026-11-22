"""``python -m champion.tui``: start the TUI (until ``champion tui`` lands, CR-T9-2).

Same start options as ``champion chat``: ``--config``, ``--level``, ``--read``,
``--edit``, ``--outside``. The runtime is built as in ``champion.cli``.
"""

from __future__ import annotations

import argparse
import sys

from champion.core.config import ACCESS_LEVELS, ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m champion.tui", description="Champion terminal UI")
    parser.add_argument("--config", help="config file (default: CHAMPION_CONFIG or config/config.yaml)")
    parser.add_argument("--level", choices=ACCESS_LEVELS,
                        help="access level for this session (default: policy.default_level)")
    parser.add_argument("--read", action="append", default=[], metavar="FOLDER",
                        help="also allow reading FOLDER in this session (repeatable)")
    parser.add_argument("--edit", action="append", default=[], metavar="FOLDER",
                        help="also allow editing FOLDER in this session (repeatable)")
    parser.add_argument("--outside", choices=("none", "read", "edit"),
                        help="right for everything else outside the working folder and the listed folders")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from champion.tui.app import ChampionApp
    except ImportError as exc:
        print(f"Cannot start the TUI: {exc}. Install it with: pip install 'textual>=0.80,<2'", file=sys.stderr)
        return 2
    from champion.cli import access_file
    from champion.core.runtime import ChampionRuntime, RuntimeSetupError
    from champion.policy.access import AccessError

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    try:
        runtime = ChampionRuntime(config, level=args.level, access_file=access_file(config),
                                  read=tuple(args.read), edit=tuple(args.edit), outside=args.outside)
    except AccessError as exc:
        print(f"Access error: {exc}", file=sys.stderr)
        return 2
    except RuntimeSetupError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2
    ChampionApp(runtime).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
