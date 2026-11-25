"""CR-1: core tests do not see the agents of track packages (tests/conftest.py)."""

from dataclasses import replace
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


# ------------------------------------------- CR-1 follow-up: config sections and knowledge bases (M1)

def collection(name: str):
    from champion.knowledge.registry import KnowledgeCollection

    return (KnowledgeCollection(name, model="test-model"),)


def test_a_track_section_and_knowledge_base_are_hidden_and_test_ones_stay(monkeypatch, config, tmp_path):
    from champion.core import config as cfg
    from champion.knowledge import registry as knowledge
    from champion.knowledge import status
    from champion.knowledge.root import KnowledgeError

    register_specialist(replace(spec("fake_track"), config_section="fake_track",
                                config_defaults={"knowledge_root": ""}))  # a track with section and base
    knowledge.register_knowledge(knowledge.KnowledgeSpec("fake_track", collection("fake_docs")))
    try:
        assert "fake_track" in {s.name for s in cfg.config_sections()}  # the track is registered
        assert hide_track_agents(monkeypatch) == {"fake_track"}
        assert "fake_track" not in {s.name for s in cfg.config_sections()}
        assert "fake_track" not in {s.agent for s in knowledge.knowledge_specs()}
        assert "fake_track" not in {s.agent for s in status.knowledge_specs()}  # imported by name: hidden too
        with pytest.raises(KnowledgeError, match="no knowledge base is declared for fake_track"):
            knowledge.knowledge_spec("fake_track")
        loaded = load_config(tmp_path / "config.yaml", env={}, cwd=tmp_path)
        assert loaded.get("fake_track") is None
        assert cfg.example_fragment_problems(tmp_path / "none") == []
        assert [(line.name, line.status) for line in status.knowledge_status(loaded)] == [("Knowledge", "OK")]

        register_specialist(replace(spec("test_only"), config_section="test_only",
                                    config_defaults={"greeting": "hi"}))  # registered by the test: visible
        knowledge.register_knowledge(knowledge.KnowledgeSpec("test_only", collection("test_docs")))
        assert {s.name for s in cfg.config_sections()} >= {"test_only"}
        assert knowledge.knowledge_spec("test_only").agent == "test_only"
        assert [s.agent for s in status.knowledge_specs()] == ["test_only"]
        assert load_config(tmp_path / "config.yaml", env={}, cwd=tmp_path).get("test_only.greeting") == "hi"
    finally:
        unregister_specialist("fake_track")
        unregister_specialist("test_only")
        knowledge.unregister_knowledge("fake_track")
        knowledge.unregister_knowledge("test_only")


@pytest.mark.track_agents
def test_a_marked_core_test_sees_the_track_packages():
    from champion.core import config as cfg
    from champion.knowledge import registry as knowledge

    registry.load_builtin_specialists()
    original = registry.registered_specialists
    assert getattr(original, "__name__", "") == "registered_specialists"  # not the filtered function
    assert cfg.config_sections.__name__ == "config_sections"
    assert knowledge.knowledge_specs.__name__ == "knowledge_specs"
    assert knowledge.knowledge_spec.__name__ == "knowledge_spec"
