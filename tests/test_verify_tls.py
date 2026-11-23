"""llm.verify_tls: certificate check of the model server (like curl -k when false).

The user's vLLM server (apps.calypso.intra.chrysler.com) sends only its own certificate,
so Python cannot verify it without the Stellantis CA files.
"""

import asyncio

import pytest
from agents import Agent
from fake_model import FakeModel, text

import champion.core.runtime as runtime_module
from champion.chat import chat_loop
from champion.core.config import ConfigError, load_config
from champion.core.runtime import ChampionRuntime, build_client
from champion.doctor import check_config
from champion.storage.audit import AuditLog

VLLM = "https://apps.calypso.intra.chrysler.com/vllm-gpt-oss-120b/v1"


def make_config(tmp_path, llm):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("llm:\n  model_id: openai/gpt-oss-120b\n  api_key_env: VLLM_API_KEY\n" + llm)
    return load_config(folder / "config.yaml", env={"VLLM_API_KEY": "none"}, cwd=tmp_path)


def test_default_is_on_and_the_key_is_known(tmp_path):
    config = make_config(tmp_path, f"  base_url: {VLLM}\n  verify_tls: true\n")
    assert config.get("llm.verify_tls") is True
    assert not any("verify_tls" in warning for warning in config.warnings)  # live: "Unknown config key"
    assert not [r for r in check_config(config) if r.name == "TLS certificate check"]


@pytest.mark.parametrize("llm, message", [
    ("  verify_tls: false\n", "needs llm.base_url"),
    ("  verify_tls: false\n  base_url: https://api.openai.com/v1\n", "must be true"),
    ("  verify_tls: false\n  provider: databricks\n  base_url: https://w.cloud.databricks.com/ai-gateway/mlflow/v1\n",
     "must be true"),
    ("  verify_tls: 'no'\n", "llm.verify_tls must be true or false"),
])
def test_off_only_for_an_internal_base_url(tmp_path, llm, message):
    with pytest.raises(ConfigError, match=message):
        make_config(tmp_path, llm)


def test_client_without_certificate_check_and_doctor_warning(tmp_path, monkeypatch):
    seen = []
    real = runtime_module.DefaultAsyncHttpxClient
    monkeypatch.setattr(runtime_module, "DefaultAsyncHttpxClient",
                        lambda **kwargs: seen.append(kwargs) or real(**kwargs))
    off = make_config(tmp_path, f"  base_url: {VLLM}\n  verify_tls: false\n")
    client = build_client(off, env={"VLLM_API_KEY": "none"})
    assert seen == [{"verify": False}] and str(client.base_url).startswith(VLLM)
    [warning] = [r for r in check_config(off) if r.name == "TLS certificate check"]
    assert warning.status == "WARN" and VLLM in warning.detail
    seen.clear()
    build_client(make_config(tmp_path, f"  base_url: {VLLM}\n"), env={"VLLM_API_KEY": "none"})
    assert seen == []  # on (default): the normal client, certificates checked


def test_chat_start_line_says_the_check_is_off(tmp_path):
    config = make_config(tmp_path, f"  base_url: {VLLM}\n  verify_tls: false\n")
    runtime = ChampionRuntime(config, model=FakeModel([text("ok")]), audit=AuditLog(),
                              agent_factory=lambda m: Agent(name="Champion", model=m))
    out = []

    def read_line(prompt):
        raise EOFError
    asyncio.run(chat_loop(runtime, read_line, out.append))
    assert any("TLS certificate check is off" in line for line in out)
