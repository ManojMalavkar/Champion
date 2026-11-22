# T9.1 - TUI shell

Track T9 (`track/tui`), plan in `docs/tracks/tui.md`. First sub-step of milestone 1.

## What was built
* `champion/tui/app.py` - `ChampionApp(runtime)`: header (M logo, title, Working and Base rows), six tabs (Conversation, Settings, Paths & Access, Modified Files, Session History, HPC Jobs), right panel, footer. Minimum size 90x24 (a message is shown below that); right panel hidden below 110 columns, F6 toggles it; Ctrl+Q quits.
* `champion/tui/state.py` - `ChampionUIState` view model, built only from the core: model id, `runtime.total_usage`, agents from the registry (orchestrator, specialists, group members), Read/Edit path counts from `context.level` (working folder + listed folders; Edit counts as Read; "everything else" is not a path), Files affected = unique files in this session's audit `changes` (a rename follows the file).
* `champion/tui/widgets.py` - `Panel` (redraws only when its text changes), `SourceRow`, `AgentUsage`, `AgentSoftware`, `ResourceUsage`, `ContextSummary`, `StatusFooter` (clock redrawn every second, nothing else).
* `champion/tui/resources.py` - host RAM (`/proc/meminfo`: MemTotal, MemAvailable) and free disk of the Working path's filesystem, else the Champion working folder (labelled); warning above 85% RAM / below 10% free disk, critical above 95% / below 5%.
* `champion/tui/logo.py` - `LogoState`: draws `assets/m_logo.png` with half blocks when Pillow is installed (white background removed, cropped to the logo), else a text M in the logo's blue-to-cyan gradient; Error = red text M. Thinking/Working animation (`m_logo.gif`) comes in T9.2. The logo files were pushed by the user (commit 4004ad8): `m_logo.png` 1254x1254 RGBA with a transparent background; `m_logo.gif` 128x128, 63 frames of 40 ms, looping, frame 0 empty. The header draws the PNG at 12x5 cells: at 10x4 the M's top notch and the gap between its legs were lost.
* `champion/tui/__main__.py` - `python -m champion.tui` with the same start options as `champion chat`.
* Tabs whose core piece is not built say "not available yet (needs ...)"; no mock data. Only the agents that exist today (orchestrator, Coder, Linux) appear; new tracks appear without TUI changes.

## Not in T9.1
Questions and answers (T9.3), logo pulse (T9.2), action blocks and approval (T9.4), agent states from events (T9.5), access editing (T9.6), Modified Files table (T9.7), Working/Base selection (T9.8).

## Verify
* `pytest tests/tui` - 11 tests: state counts, tabs and footer, too-small message, F6, clock-only redraw, context update, logo fallback, PNG half blocks, the shipped logo files, RAM/disk thresholds, start options.
* Full suite on Databricks serverless (textual 1.0.0, openai-agents 0.25.0, Pillow 12.0.0): 280 passed.
* Linux: `PYTHONPATH=<Champion-tui worktree> python -m champion.tui`; Pillow must be in the venv for the image logo (else the text M).
