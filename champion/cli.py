"""Champion command line.

``champion --version``, ``champion doctor`` (step 0), ``champion chat``
(steps 1-2a) and ``champion kb status`` / ``champion kb build`` (M1: the
knowledge bases the agents declare). The terminal UI is track T9.
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

    kb = commands.add_parser("kb", help="knowledge bases of the agents: status, build")
    kb.add_argument("action", choices=("status", "build"))
    kb.add_argument("--agent", help="only this agent (build: required)")
    kb.add_argument("--collection", action="append", default=[],
                    help="build only this collection (repeatable; default: every one the agent can build)")
    kb.add_argument("--verify", action="store_true",
                    help="status: also compare the source files with the SHA-256 in MANIFEST.json")
    kb.add_argument("--replace", action="store_true", help="build: build an existing collection again")
    kb.add_argument("--allow-foreign", action="store_true",
                    help="build: write into a database without MANIFEST.json (not built by Champion)")

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

    if args.command == "kb":
        return run_kb(config, args)

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


def run_kb(config, args) -> int:
    """``champion kb status|build`` (M1). Exit code 1 when a check fails or a build is refused."""
    from champion.knowledge.root import KnowledgeError
    from champion.knowledge.status import FAIL, knowledge_status, render_lines

    if args.action == "status":
        lines = knowledge_status(config, args.agent, verify=args.verify)
        print(render_lines(lines))
        return 1 if any(line.status == FAIL for line in lines) else 0

    from champion.knowledge.builder import build_collection
    from champion.knowledge.registry import knowledge_spec

    if not args.agent:
        print("champion kb build needs --agent NAME", file=sys.stderr)
        return 2
    try:
        spec = knowledge_spec(args.agent)
        names = args.collection or [c.name for c in spec.collections if c.documents is not None]
        if not names:
            raise KnowledgeError(f"{args.agent} declares no collection that can be built")
        for name in names:
            print(f"{args.agent}/{name}: building ...")
            info = build_collection(config, args.agent, name, replace=args.replace, allow_foreign=args.allow_foreign,
                                    progress=lambda done, total: print(f"  {done}/{total}", end="\r", flush=True))
            print(f"\n{args.agent}/{name}: {info.count} entries, model {info.model} ({info.dimension}, {info.space})")
    except KnowledgeError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
