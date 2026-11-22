# T9.3 - Conversation tab

Track T9 (`track/tui`), plan in `docs/tracks/tui.md`. Built after the user's first run of T9.1 ("there is no user input at bottom"), 22 Nov 2026.

## What was built
* `champion/tui/conversation.py`
  * `ChatInput` (TextArea): Enter sends; Shift+Enter or Ctrl+J adds a line (many terminals send Shift+Enter as plain Enter, so Ctrl+J always works); a paste stays one message; Up/Down recall earlier inputs while the box is empty or shows a recalled input.
  * `ConversationView`: your text right-aligned in the accent colour, answers left-aligned as Markdown, no speaker labels. Not focusable, so the focus stays in the input.
  * `ActionBlock`: the live tool lines of one request (`chat.format_event`), BLOCKED/ERROR in red; at the end `✓ Completed · Linux · 3 tool calls · 4.2 s` (or Failed / Stopped). Removed when the request used no tool. Collapsing comes in T9.4.
  * `TeeRuntime`: gives `chat.answer` the runtime, but every tool event also reaches the TUI first (for the logo state and the block).
* `champion/tui/app.py`
  * The request runs in a Textual thread worker with its own event loop and goes through `champion.chat.answer`, so error texts, the max-turns stop, the tokens line `[N tokens, M model call(s)]` and the change summary are the chat's own.
  * One request at a time: the input is disabled while it runs and the hint says `working... Ctrl+C cancels this request`.
  * Ctrl+C cancels the request (the task is cancelled in its loop) and shows `Stopped by the user.`; it never quits the app. Ctrl+Q quits.
  * Logo state: thinking (request running, no tool open), working (a tool runs), idle, error (red M; the request ended with an error). The title shows `mChampion · thinking` etc. The pulse/GIF comes in T9.2.
  * Slash commands (`/access`, `/changes`, ...) are not sent to the model; a note says they come later (T9.5). `/exit` and `/quit` close the TUI. A line like `/CAE/model what is in it?` is a normal question.
* Approvals: the TUI has no approver yet (T9.4), so a command that needs approval is refused by the policy ("not possible here") and nothing runs.

## Verify
* `pytest tests/tui` - 19 tests (8 new in `test_tui_conversation.py`): answer left / question right / no labels / tokens line; tool lines and Completed header with thinking -> working -> idle; error note + red logo + next request works; Ctrl+C stops a slow request without quitting; Enter vs Ctrl+J / Shift+Enter; Up/Down history; slash commands not sent; a delete via the Linux agent is BLOCKED and the file stays.
* TUI tests 3 runs in a row: 19 passed each; full suite on serverless: 288 passed.

## Linux check (user)
```
source ~/.ansa_meta_copilot/.venv/bin/activate
cd /nobackup/people/t0018k6/script/Github_repo/LLM/Champion-tui
git pull
PYTHONPATH=$PWD python -m champion.tui
```
1. Ask "Which access level am I in?" - question right, answer left, a Completed line with 1 tool call.
2. Ask the Linux agent to list files in the working folder - `[Linux]` lines in the block.
3. Paste 10 lines - one message; Ctrl+J adds a line.
4. Ask something long and press Ctrl+C - `Stopped by the user.`, the app stays open.
5. Ask to delete a file - refused (no approval dialog until T9.4), the file stays.
