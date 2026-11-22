# Track T9 - TUI

Branch `track/tui`, package `champion/tui/`, tests `tests/tui/`. Status: T9.1 can start.

## Goal
A Textual terminal UI (former step 6 / M4) next to the plain chat, which stays. Moved out of `main` into its own track (user, 20 Nov 2026). The TUI reuses `ChampionRuntime` and the chat functions (`format_event`, `level_command`, `audit_command`, `turn_summary`, access and changes commands), so behaviour and safety stay identical; it only adds screens. Agents and sub-agents come from the registry (`runtime.context.specialists`, `sub_agents`), so new tracks appear without TUI changes. `textual>=0.80,<2` is already a dependency.

## Sub-steps
- [ ] T9.1 App: chat panel (Markdown answers), input line, status bar (model, access level, tokens); started with `python -m champion.tui`. Verify: Textual pilot test (headless) with the fake model shows the answer.
- [ ] T9.2 Tool-line panel: live tool calls of every agent, sub-agents indented (`Group/Member`). Verify: group run from the M0 demo shows three levels.
- [ ] T9.3 Approval dialog through `ChampionContext.approver`; "no" ends the turn with the fixed answer; typed-ahead input does not answer the dialog. Verify: pilot test for yes and no.
- [ ] T9.4 Panels: access (levels and folders), changes with undo, audit, agents and sub-agents.
- [ ] T9.5 Slash commands as in the chat (`/access`, `/changes`, `/undo`, `/audit`, `/new`, `/help`), reusing the chat functions.
- [ ] T9.6 `champion tui` command (core request); memory panel later (needs M6).

## Decisions
* The plain chat (`champion chat`) stays; the TUI is an extra front end.
* No logic is copied from `chat.py`: if a function is missing or not reusable, write a core request instead of a second version.

## Core requests (CR)
* `champion tui` subcommand in `cli.py` (T9.6).
* Possibly: an async approval hook, if a blocking `approver` callable stalls the Textual event loop (found in T9.3).

## Findings and live checks
None yet.
