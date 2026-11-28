# CR-7: `llm.api` - choose the model API (Responses or Chat Completions)

Shared core change on `main` (T7 Core), asked for by T1 PyDyna. Built 28 Nov 2026.

## Why

Linux live check of T1.2, 28 Nov 2026 (`atlas/gpt-oss-120b`, `https://genai.atlas.intra.chrysler.com/v1`,
LiteLLM 1.101.0 in front of vLLM). Every PyDyna Code run ended with
`ERROR: specialist PyDyna/Code failed (RateLimitError)`, always on the third model call.

What the diagnosis showed (user, Linux):

* Not a rate limit: 8 calls in 2.6 s and a 12k-token request all returned 200, with no rate-limit headers.
* The Code sub-agent run alone, with the HTTP traffic printed: call 1 returned
  `function_call pydyna_template`; call 2 returned the same tool call as an **`mcp_call`** item
  (`"server_label": "functions"`). The Agents SDK takes `mcp_call` for a hosted MCP call, so it never
  ran the tool, and it sent the item back as input; vLLM refused it with
  `Unknown input type: mcp_call` (400). LiteLLM then tried its fallback group
  `hesperides/gpt-oss-120b`, found no deployment, and answered **429**.
* The same task with the Chat Completions API: all three calls 200, and the tool calls
  (`search_pydyna_api`, `lookup_keyword`) were run normally.

So the cause is the server's Responses API, not Champion and not T1.2. Chat Completions has no
`mcp_call` items.

## Change

* `llm.api` (new key in `DEFAULTS`, `core/config.py`): `auto` (default), `responses` or `chat`;
  case and spaces are ignored.
  * `auto` keeps the behaviour from before CR-7: `provider: openai` uses the Responses API,
    `provider: databricks` uses Chat Completions (AI Gateway).
  * Any other value is refused (`llm.api must be one of auto, responses, chat`).
  * `provider: databricks` with `api: responses` is refused: the AI Gateway is used with Chat
    Completions only.
* `model_api(config)` in `core/config.py` gives `"chat"` or `"responses"`. It lives in the config
  module so the doctor can use it without importing the Agents SDK.
* `build_model` (`core/runtime.py`): `OpenAIChatCompletionsModel` for `chat`, otherwise
  `OpenAIResponsesModel` (as before).
* `champion doctor`: new line `Model API  Chat Completions API (llm.api: chat)`;
  `doctor --online` now tests the API the chat uses (before: chosen by the provider only). The label
  for `provider: databricks` stays `Chat Completions API (AI Gateway)`.
* `config/config.example.yaml`: `api: auto` with when to use `chat`.

Not changed: no rewriting of `mcp_call` items into `function_call` (it would patch a server bug inside
the SDK's data); no retries for 429 (the 429 was not a rate limit).

## Tests

`tests/test_llm_api.py`, 11 cases:

* `auto`: openai gives `OpenAIResponsesModel`, databricks gives `OpenAIChatCompletionsModel`;
* `chat`, `'Chat '` and `responses` choose the model class; the key gives no "Unknown config key";
* databricks with `chat` allowed;
* `completions`, `1` and databricks with `responses` refused;
* the doctor line for `auto` and `chat`;
* `doctor --online` with `chat` calls Chat Completions (a Responses call would fail).

Mutation check: with `build_model` choosing by provider only again, 2 of the 11 fail.
Serverless, `main` `c7cbea5` plus this change: 489 passed.

## Linux check (user)

After `main` is merged into `track/pydyna` and pulled:

1. In `Champion-pydyna/config/config.yaml` (now a real file, not a link) add under `llm:`
   `api: chat`.
2. `champion doctor --online`: `Model API  Chat Completions API (llm.api: chat)` and
   `Model atlas/gpt-oss-120b  Chat Completions API succeeded`.
3. `pytest -q`: all pass.
4. The live level-1.2 chat of T1.2 again (`docs/steps/pydyna-10-code-agent.md`): the Code sub-agent
   gets past `pydyna_template` without `RateLimitError`.

## Status

Built 28 Nov 2026 on `main` (working tree), not committed; waiting for the user's OK.
