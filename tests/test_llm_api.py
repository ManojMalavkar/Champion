"""CR-7 llm.api: which model API Champion uses (auto, responses or chat).

Linux live check 28 Nov 2026 (atlas/gpt-oss-120b behind LiteLLM 1.101.0 and vLLM): with the Responses
API the server returned a call of Champion's own tool pydyna_template as an "mcp_call" item
(server_label "functions"); the SDK sent it back and vLLM refused it ("Unknown input type: mcp_call",
400, passed on by LiteLLM as 429 RateLimitError). The same task with Chat Completions: all calls 200.
"""

import sys
import types

import pytest
from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel

from champion import doctor
from champion.core.config import ConfigError, load_config, model_api
from champion.core.runtime import build_client, build_model

ATLAS = "https://genai.atlas.intra.chrysler.com/v1"
GATEWAY = "https://adb-1.azuredatabricks.net/ai-gateway/mlflow/v1"
ENV = {"LLM_KEY": "none"}


def make_config(tmp_path, llm):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: atlas/gpt-oss-120b\n  api_key_env: LLM_KEY\n" + llm)
    return load_config(folder / "config.yaml", env=ENV, cwd=tmp_path)


def model_class(config):
    return type(build_model(config, build_client(config, env=ENV)))


def test_default_auto_keeps_each_providers_api(tmp_path):
    openai = make_config(tmp_path, f"  base_url: {ATLAS}\n")
    assert openai.get("llm.api") == "auto" and model_api(openai) == "responses"
    assert model_class(openai) is OpenAIResponsesModel
    databricks = make_config(tmp_path, f"  provider: databricks\n  base_url: {GATEWAY}\n")
    assert model_api(databricks) == "chat" and model_class(databricks) is OpenAIChatCompletionsModel


@pytest.mark.parametrize("value, api, cls", [
    ("chat", "chat", OpenAIChatCompletionsModel),
    ("Chat ", "chat", OpenAIChatCompletionsModel),
    ("responses", "responses", OpenAIResponsesModel),
])
def test_explicit_api_selects_the_model_class(tmp_path, value, api, cls):
    config = make_config(tmp_path, f"  base_url: {ATLAS}\n  api: '{value}'\n")
    assert not any("api" in warning for warning in config.warnings)  # a known key, no "Unknown config key"
    assert model_api(config) == api and model_class(config) is cls


def test_databricks_with_chat_is_allowed(tmp_path):
    config = make_config(tmp_path, f"  provider: databricks\n  base_url: {GATEWAY}\n  api: chat\n")
    assert model_class(config) is OpenAIChatCompletionsModel


@pytest.mark.parametrize("llm, message", [
    ("  api: completions\n", "llm.api must be one of auto, responses, chat"),
    ("  api: 1\n", "llm.api must be one of"),
    (f"  provider: databricks\n  base_url: {GATEWAY}\n  api: responses\n", "not possible when llm.provider is databricks"),
])
def test_unknown_or_impossible_values_are_refused(tmp_path, llm, message):
    with pytest.raises(ConfigError, match=message):
        make_config(tmp_path, f"  base_url: {ATLAS}\n" + llm if "provider" not in llm else llm)


@pytest.mark.parametrize("llm, detail", [
    (f"  base_url: {ATLAS}\n", "Responses API (llm.api: auto)"),
    (f"  base_url: {ATLAS}\n  api: chat\n", "Chat Completions API (llm.api: chat)"),
])
def test_doctor_shows_the_api(tmp_path, llm, detail):
    [line] = [r for r in doctor.check_config(make_config(tmp_path, llm)) if r.name == "Model API"]
    assert line.status == doctor.OK and line.detail == detail


def test_doctor_online_check_uses_the_chat_api(tmp_path, monkeypatch):
    """With llm.api: chat the doctor tests Chat Completions, the API the chat will use."""
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())
            self.responses = None  # a Responses call would fail

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeClient))
    results = doctor.check_online(make_config(tmp_path, f"  base_url: {ATLAS}\n  api: chat\n"), env=ENV)
    assert [(r.name, r.status, r.detail) for r in results] == [
        ("Model atlas/gpt-oss-120b", doctor.OK, "Chat Completions API succeeded")]
    assert calls[0]["model"] == "atlas/gpt-oss-120b" and calls[0]["messages"][0]["role"] == "user"
