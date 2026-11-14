# Step 1 - First agent in a plain command-line chat

## What was built

| File | Purpose |
| --- | --- |
| `champion/core/context.py` | `ChampionContext`: data the tools can use but the model never sees (config, working directory, access level) |
| `champion/tools/session_info.py` | First tool, `session_info`: version, time, folder, level, specialists, model. Reads no files |
| `champion/agents/assistant.py` | The `Champion` agent: instructions + one tool |
| `champion/core/hooks.py` | `ToolEventRecorder`: run hooks that record and report every tool call |
| `champion/core/runtime.py` | `ChampionRuntime`: OpenAI client, model, run config, session, `Runner.run` |
| `champion/chat.py` | Plain chat loop (`/help`, `/new`, `/usage`, `/exit`) and one-shot mode |
| `champion/cli.py` | New command `champion chat [-q QUESTION]` |
| `tests/fake_model.py` | Scripted model for tests (no network, no key) |
| `tests/test_runtime.py`, `tests/test_chat.py` | 16 new tests (56 in total) |
| config | New key `orchestration.history_turns` (default 10) |

## Key concepts

**Agent = configuration, Runner = loop.** `Agent(name, instructions, tools, model)` only describes the agent. `Runner.run(agent, input, ...)` runs the loop you wrote by hand in lab 3: send instructions + history + tool schemas to the model; if it asks for tool calls, run them and send the results back; repeat until it answers with text. `max_turns` (`orchestration.max_steps`, 8) stops a model that keeps calling tools (`MaxTurnsExceeded`).

**`@function_tool`.** Turns a Python function into a tool: name = function name, description = docstring, argument schema = type hints. The docstring is what the model reads to decide when to call it, so it is written for the model. The first parameter `ctx: RunContextWrapper[ChampionContext]` is filled in by the SDK and is not in the schema, so the model cannot set it.

**Context vs. input.** `context=` in `Runner.run` is Champion's own object, passed to tools and hooks, never sent to the model. Only what a tool returns reaches the model. In step 2 the access level in the context is what every file tool checks; the model cannot change it.

**Hooks.** `RunHooks.on_tool_start/on_tool_end` are called around every tool call with a `ToolContext` (call id, JSON arguments). `ToolEventRecorder` prints `-> tool(args)` / `<- tool: N chars` in the chat; later the TUI and the audit log use the same events.

**Session memory.** `SQLiteSession` (in memory) stores the conversation; `Runner.run(..., session=...)` adds it to each request and saves the new items. `keep_last_turns` (the `session_input_callback`) sends only the last `history_turns` turns, cutting at user messages so a tool call is never separated from its result. `/new` starts a fresh session.

**One client, explicit model.** `build_client` creates one `AsyncOpenAI` with the key from step 0; `OpenAIResponsesModel(model=..., openai_client=client)` uses it. Nothing is set globally and the key is not in `os.environ`. Tracing is disabled unless `llm.tracing: true`.

**Testing without OpenAI.** `FakeModel` implements the SDK `Model` interface and returns scripted outputs (a tool call, then text). It also records what the SDK sent, so tests check the real loop: the tool result goes back to the model, history is remembered and trimmed, `/new` forgets, `max_turns` stops a loop.

## Code flow of one question

1. `chat_loop` reads a line and calls `answer` -> `runtime.ask(text)`.
2. `ask` creates a `ToolEventRecorder` and calls `Runner.run(agent, text, context, max_turns, hooks, run_config, session)`.
3. The SDK loads the session history, trims it with `keep_last_turns`, calls the model.
4. Model asks for `session_info` -> hooks print `->` -> tool runs with `ctx.context` -> hooks print `<-` -> result sent back -> model answers.
5. `ask` returns `TurnResult(text, tool_events, usage)`; the chat prints the answer and the token count.
6. Errors (API, max turns) are printed in one line and the chat continues. API errors show only type and HTTP status.

## How to verify

```bash
pytest                                    # expect 56 passed
champion chat -q "What time is it and which folder am I in?"
champion chat
```

In the chat, try:
* "What is my access level?" -> you should see `-> session_info({})` before the answer.
* "My name is Manoj", then "What is my name?" -> remembered; `/new`, then ask again -> forgotten.
* "List the files in this folder" -> it should say this is not available yet (no file tools until step 2).
* `/usage` -> tokens used so far.
* Set an invalid key -> one line `AuthenticationError (HTTP 401) from the OpenAI API`, the chat keeps running.

## Verified here

Databricks serverless (Python 3.12, openai-agents 0.24.0): 56 tests passed; a scripted chat showed the tool call, answer and usage lines; the real `OpenAIResponsesModel` with an invalid key reached the OpenAI API and was reported as `AuthenticationError (HTTP 401)`. The SDK also logs "Error getting response" to stderr in that case. A real answer with your key and model still needs checking on your machine.
