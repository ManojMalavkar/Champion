"""CR-1: core tests do not see the agents of track packages (tests/conftest.py)."""

from pathlib import Path

import pytest
from conftest import CORE_AGENTS, hide_track_agents

from champion.core.config import load_config
from champion.specialists import registry
from champion.specialists.base import SpecialistSpec
from champion.specialists.registry import register_specialist, select_specialists, unregister_specialist


def spec(name: str) -> SpecialistSpec:
    return SpecialistSpec(name=name, title=name.title(), description="d", instructions="i")


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "work").mkdir()
    (tmp_path / "config.yaml").write_text(
        f"llm:\n  model_id: m\nexecution:\n  working_directory: '{tmp_path / 'work'}'\n")
    return load_config(tmp_path / "config.yaml", env={}, cwd=tmp_path)


def test_core_tests_see_only_the_core_agents():
    assert {s.name for s in registry.registered_specialists()} == CORE_AGENTS


def test_a_track_agent_is_hidden_and_a_test_agent_stays(monkeypatch, config):
    register_specialist(spec("fake_track"))  # stands for an agent found in champion/specialists/<track>/
    try:
        assert hide_track_agents(monkeypatch) == {"fake_track"}
        register_specialist(spec("test_only"))  # registered by a test after the hiding: visible
        names = [s.name for s in registry.registered_specialists()]
        assert "fake_track" not in names and "test_only" in names
        assert set(CORE_AGENTS) <= set(names)
        selected, _ = select_specialists(config)
        assert [s.name for s in selected] == ["coder", "linux_coder", "test_only"]
    finally:
        unregister_specialist("fake_track")
        unregister_specialist("test_only")
