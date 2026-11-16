"""Tests for champion doctor and the CLI (step 0)."""

import os
import sys
import types
from pathlib import Path

import pytest

from champion import cli, doctor
from champion.core.config import load_config
from champion.doctor import FAIL, OK, WARN

SECRET = "sk-test-THIS-MUST-NEVER-BE-PRINTED"


def make_config(tmp_path: Path, text: str = "llm:\n  model_id: test-model\n"):
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    path = folder / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, env={}, cwd=tmp_path)


def by_name(results, name):
    return [r for r in results if r.name == name]


def test_key_is_never_printed(tmp_path, capsys):
    config = make_config(tmp_path)
    results = doctor.run_checks(config, env={"OPENAI_API_KEY": SECRET})
    doctor.render(results)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert all(SECRET not in r.detail for r in results)
    assert by_name(results, "OpenAI key")[0].status == OK


def test_missing_key_fails(tmp_path):
    results = doctor.check_api_key(make_config(tmp_path), env={})
    assert results[0].status == FAIL


def test_configured_key_environment_name(tmp_path):
    config = make_config(tmp_path, "llm:\n  api_key_env: DATABRICKS_TOKEN\n")
    results = doctor.check_api_key(config, env={"DATABRICKS_TOKEN": SECRET})
    assert results[0].status == OK
    assert SECRET not in results[0].detail


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX only")
def test_dotenv_readable_by_others_warns(tmp_path):
    config = make_config(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"OPENAI_API_KEY={SECRET}\n")
    dotenv.chmod(0o644)
    results = doctor.check_api_key(config, env={})
    assert results[0].status == OK and ".env" in results[0].detail
    assert by_name(results, ".env permissions")[0].status == WARN
    dotenv.chmod(0o600)
    assert by_name(doctor.check_api_key(config, env={}), ".env permissions") == []


def test_placeholder_model_fails(tmp_path):
    results = doctor.check_config(make_config(tmp_path, "llm:\n  model_id: REPLACE_WITH_MODEL_ID\n"))
    assert by_name(results, "Model")[0].status == FAIL


def test_tracing_on_warns(tmp_path):
    results = doctor.check_config(make_config(tmp_path, "llm:\n  model_id: m\n  tracing: true\n"))
    assert by_name(results, "SDK tracing")[0].status == WARN


def test_config_warnings_are_reported(tmp_path):
    results = doctor.check_config(make_config(tmp_path, "llm:\n  model_id: m\n  typo: 1\n"))
    assert by_name(results, "Config key")[0].status == WARN


def test_packages_required_fail_optional_warn():
    missing = {"agents", "chromadb"}
    results = doctor.check_packages(lambda name: None if name in missing else object())
    assert by_name(results, "Package openai-agents")[0].status == FAIL
    assert by_name(results, "Package chromadb")[0].status == WARN
    assert by_name(results, "Package rich")[0].status == OK


def test_paths(tmp_path):
    db = tmp_path / "vector_db"
    db.mkdir()
    (db / "chroma.sqlite3").write_text("")
    config = make_config(tmp_path, f"llm:\n  model_id: m\npydyna:\n  database_path: {db}\n"
                                   f"  keyword_reference_path: missing.jsonl\n")
    results = doctor.check_paths(config)
    assert by_name(results, "Working directory")[0].status == OK
    assert by_name(results, "Audit database")[0].status == OK
    assert by_name(results, "PyDyna vector DB")[0].status == OK
    assert by_name(results, "Keyword reference")[0].status == WARN
    assert by_name(results, "PyDyna Python")[0].status == WARN


def test_missing_working_directory_fails(tmp_path):
    config = make_config(tmp_path, "llm:\n  model_id: m\nexecution:\n  working_directory: does/not/exist\n")
    assert by_name(doctor.check_paths(config), "Working directory")[0].status == FAIL


def test_online_check_reports_error_type_only(tmp_path, monkeypatch):
    class FakeError(Exception):
        status_code = 401

    class FakeResponses:
        def create(self, **kwargs):
            raise FakeError(f"bad key {SECRET}")

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == SECRET
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeClient))
    config = make_config(tmp_path, "  api_key_env: DATABRICKS_TOKEN\n")
    result = doctor.check_online(config, env={"DATABRICKS_TOKEN": SECRET})
    assert result.status == FAIL
    assert "FakeError (HTTP 401)" in result.detail
    assert SECRET not in result.detail


def test_online_check_ok(tmp_path, monkeypatch):
    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["max_output_tokens"] == 16
            assert kwargs["input"][0]["role"] == "user"
            return object()

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeClient))
    assert doctor.check_online(make_config(tmp_path), env={"OPENAI_API_KEY": SECRET}).status == OK


def test_online_check_without_key(tmp_path):
    assert doctor.check_online(make_config(tmp_path), env={}).status == FAIL


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert "champion 0.1.0" in capsys.readouterr().out


def test_cli_doctor_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.chdir(tmp_path)
    good = tmp_path / "good.yaml"
    good.write_text("llm:\n  model_id: m\n")
    bad_model = tmp_path / "placeholder.yaml"
    bad_model.write_text("llm:\n  model_id: REPLACE_WITH_MODEL_ID\n")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("policy:\n  default_level: '9.9'\n")

    assert cli.main(["--config", str(good), "doctor"]) == 0
    assert cli.main(["--config", str(bad_model), "doctor"]) == 1
    assert cli.main(["--config", str(invalid), "doctor"]) == 2
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
