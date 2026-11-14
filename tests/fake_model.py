"""A scripted model for tests: no network, no key, fully predictable.

``FakeModel`` implements the SDK's ``Model`` interface. Each call to
``get_response`` returns the next scripted output (a list of tool calls or a
text message) and records what the SDK sent, so tests can check the
instructions, tools and conversation history the real model would receive.
"""

from __future__ import annotations

import json
from typing import Any

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText


def text(message: str) -> list:
    return [ResponseOutputMessage(id="msg", type="message", role="assistant", status="completed",
                                  content=[ResponseOutputText(type="output_text", text=message, annotations=[])])]


def tool_call(name: str, arguments: dict | None = None, call_id: str = "call_1") -> list:
    return [ResponseFunctionToolCall(id=f"fc_{call_id}", call_id=call_id, type="function_call",
                                     name=name, arguments=json.dumps(arguments or {}))]


class FakeModel(Model):
    def __init__(self, outputs: list[list]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, **kwargs) -> ModelResponse:
        self.calls.append({"instructions": system_instructions, "input": input,
                           "tools": [t.name for t in tools]})
        if not self.outputs:
            raise AssertionError("FakeModel: no scripted output left")
        return ModelResponse(output=self.outputs.pop(0),
                             usage=Usage(requests=1, input_tokens=100, output_tokens=20, total_tokens=120),
                             response_id=None)

    async def stream_response(self, *args, **kwargs):  # used from step 5
        raise NotImplementedError
        yield
