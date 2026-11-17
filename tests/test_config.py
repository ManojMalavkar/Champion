"""Tests for champion.core.config (step 0)."""

import os
from pathlib import Path

import pytest
import yaml

from champion.core import config as cfg
from champion.core.config import DEFAULTS, ConfigError, get_openai_api_key, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"


def write_config(tmp_path: Path, text: str) -> Path:
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    path = folder / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def load(tmp_path: Path, text: str):
    return load_config(write_config(tmp_path, text), env={}, cwd=tmp_path)


def test_example_config_matches_defaults():
    # Keeps config.example.yaml and DEFAULTS in sync: every default is documented.
    # The llm section is the project's real setup (Databricks AI Gateway), so only
    # its keys must match; every other section must equal the defaults exactly.
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert set(raw) == set(DEFAULTS)
    assert set(raw["llm"]) == set(DEFAULTS["llm"])
    assert {k: v for k, v in raw.items() if k != "llm"} == {k: v for k, v in DEFAULTS.items() if k != "llm"}


def test_example_config_uses_the_ai_gateway(tmp_path):
    config = load(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    assert config.get("llm.provider") == "databricks"  # "DATABRICKS" in the file
    assert "/ai-gateway/mlflow/v1" in config.get("llm.base_url")
    assert config.get("llm.api_key_env") == "DATABRICKS_TOKEN"
    assert config.get("llm.model_id").startswith("system.ai.")
    assert all(m.startswith("system.ai.") for m in config.get("llm.model_ids"))


def test_example_config_loads_without_warnings(tmp_path):
    config = load(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    assert config.warnings == []
    assert config.get("policy.default_level") == "1.1"


def test_defaults_used_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "project_root", lambda: tmp_path)
    config = load_config(env={}, cwd=tmp_path)
    assert config.path is None
    assert config.get("llm.model_id") == cfg.PLACEHOLDER_MODEL
    assert config.get("pydyna.large_file_bytes") == 100 * 1024 * 1024
    assert config.get("storage.audit_db") == str(tmp_path / "champion.db")


def test_file_overrides_only_given_keys(tmp_path):
    config = load(tmp_path, "llm:\n  model_id: my-model\n")
    assert config.get("llm.model_id") == "my-model"
    assert config.get("llm.tracing") is False  # default kept
    assert config.get("orchestration.max_steps") == 8


def test_relative_paths(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    config = load_config(write_config(tmp_path, "pydyna:\n  database_path: kb/vector_db\n"),
                         env={}, cwd=work)
    # storage / pydyna paths: relative to the project root (parent of config/)
    assert config.root == tmp_path.resolve()
    assert config.get("pydyna.database_path") == str(tmp_path.resolve() / "kb" / "vector_db")
    assert config.get("storage.audit_db") == str(tmp_path.resolve() / "champion.db")
    # working directory: relative to where Champion is started
    assert config.get("execution.working_directory") == str(work.resolve())


def test_missing_current_directory_reports_config_error(tmp_path, monkeypatch):
    path = write_config(tmp_path, "llm:\n  model_id: test-model\n")

    def missing_cwd():
        raise FileNotFoundError("current directory removed")

    monkeypatch.setattr(Path, "cwd", missing_cwd)
    with pytest.raises(ConfigError, match="Current directory no longer exists; cd to an existing directory"):
        load_config(path, env={})


def test_unknown_key_warns_and_is_ignored(tmp_path):
    config = load(tmp_path, "llm:\n  modle_id: typo\n")
    assert any("llm.modle_id" in w for w in config.warnings)
    assert "modle_id" not in config.data["llm"]


def test_open_sections_accept_new_names(tmp_path):
    config = load(tmp_path, "agents:\n  ansa_meta:\n    enabled: false\nkb:\n  sources:\n    ansa_api: src/ansa\n")
    assert config.warnings == []
    assert config.get("agents.ansa_meta.enabled") is False
    assert config.get("agents.pydyna.enabled") is True
    assert config.get("kb.sources.ansa_api") == str(tmp_path.resolve() / "src" / "ansa")


@pytest.mark.parametrize("value, expected", [("'2.1'", "2.1"), ("1.2", "1.2"), ("Custom", "custom")])
def test_level_is_normalised(tmp_path, value, expected):
    config = load(tmp_path, f"policy:\n  default_level: {value}\n")
    assert config.get("policy.default_level") == expected


@pytest.mark.parametrize("text, message", [
    ("policy:\n  default_level: '3.1'\n", "policy.default_level"),
    ("policy:\n  custom:\n    read: working\n    write: outside\n", "cannot be wider"),
    ("policy:\n  custom:\n    read: everything\n", "policy.custom.read"),
    ("pydyna:\n  large_file_bytes: 200\n  max_model_bytes: 100\n", "large_file_bytes cannot be larger"),
    ("orchestration:\n  max_steps: true\n", "orchestration.max_steps"),
    ("orchestration:\n  max_steps: 0\n", "orchestration.max_steps"),
    ("execution:\n  require_approval: 'yes'\n", "execution.require_approval"),
    ("llm:\n  provider: bedrock\n", "llm.provider"),
    ("llm:\n  base_url: ftp://x\n", "llm.base_url"),
    ("llm:\n  provider: databricks\n", "AI Gateway URL"),
    ("llm:\n  provider: databricks\n  base_url: https://adb-1.azuredatabricks.net/serving-endpoints\n", "AI Gateway URL"),
    ("llm:\n  provider: databricks\n  base_url: https://adb-1.azuredatabricks.net/ai-gateway/mlflow/v1\n  tracing: true\n",
     "llm.tracing must be false"),
    ("llm: 5\n", "'llm' must be a mapping"),
])
def test_invalid_values_are_rejected(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, text)


def test_invalid_yaml_and_top_level(tmp_path):
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load(tmp_path, "llm: [unclosed\n")
    with pytest.raises(ConfigError, match="mapping at the top level"):
        load(tmp_path, "- a\n- b\n")


def test_missing_files_are_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml", env={})
    with pytest.raises(ConfigError, match="CHAMPION_CONFIG"):
        load_config(env={"CHAMPION_CONFIG": str(tmp_path / "nope.yaml")})


def test_champion_config_env_is_used(tmp_path):
    path = write_config(tmp_path, "llm:\n  model_id: from-env\n")
    config = load_config(env={"CHAMPION_CONFIG": str(path)}, cwd=tmp_path)
    assert config.path == path.resolve()
    assert config.get("llm.model_id") == "from-env"


def test_api_key_from_environment_first(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=from-file\n")
    assert get_openai_api_key(tmp_path, {"OPENAI_API_KEY": "from-env"}) == ("from-env", "environment")


def test_api_key_uses_configured_environment_name(tmp_path):
    assert get_openai_api_key(tmp_path, {"DATABRICKS_TOKEN": "from-env"}, "DATABRICKS_TOKEN") == (
        "from-env", "environment"
    )


def test_api_key_from_dotenv_does_not_touch_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=from-file\n")
    assert get_openai_api_key(tmp_path, {}) == ("from-file", ".env")
    assert "OPENAI_API_KEY" not in os.environ


def test_configured_api_key_name_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text("DATABRICKS_TOKEN=from-file\n")
    assert get_openai_api_key(tmp_path, {}, "DATABRICKS_TOKEN") == ("from-file", ".env")


def test_api_key_missing(tmp_path):
    assert get_openai_api_key(tmp_path, {}) == (None, "missing")
