"""Shared test setup (step M0).

Track tests live in ``tests/<track>/`` (for example ``tests/pydyna/``). This adds
``tests/`` to the import path so they can use ``from fake_model import ...`` like
the tests in this folder. Test file names must be unique across all folders
(pytest's default import mode): start them with the track name, for example
``tests/pydyna/test_pydyna_keyword.py``.

CR-1: the core tests (directly in ``tests/``) do not see the agents of track
packages (``champion/specialists/<track>/``), so a track that registers an agent
does not break them. Agents a test registers itself stay visible; track tests
(``tests/<track>/``) see every agent. Add a new core agent to ``CORE_AGENTS``.

CR-1 follow-up (24 Nov 2026, after M1): the config sections of the hidden track
agents and the knowledge bases declared before the test starts (other than by a core
agent) are hidden too, so ``load_config``, ``example_fragment_problems``,
``champion kb status`` and ``champion doctor`` see only core and test registrations.
The filtered functions replace every reference to the originals in the loaded
``champion`` modules (some modules import them by name). A core test that must see
every agent of the repository is marked ``@pytest.mark.track_agents``.
"""

import sys
from pathlib import Path

import pytest

TESTS = str(Path(__file__).resolve().parent)
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

CORE_AGENTS = frozenset({"coder", "linux_coder"})  # agents of champion/specialists/*.py


def _replace_everywhere(monkeypatch, original, replacement) -> None:
    """Replace ``original`` in every loaded ``champion`` module that holds it (``from x import f`` too)."""
    for module in list(sys.modules.values()):
        if module is None or not getattr(module, "__name__", "").startswith("champion"):
            continue
        for attribute, value in list(vars(module).items()):
            if value is original:
                monkeypatch.setattr(module, attribute, replacement)


def hide_track_agents(monkeypatch) -> set[str]:
    """Hide the agents registered now (other than the core agents), their config sections and the
    knowledge bases declared now (other than by a core agent). Returns the hidden agent names."""
    from champion.core import config as cfg
    from champion.knowledge import registry as knowledge
    from champion.knowledge.root import KnowledgeError
    from champion.specialists import registry

    registry.load_builtin_specialists()
    hidden = {spec.name for spec in registry.registered_specialists()} - CORE_AGENTS
    sections = {section.name for section in cfg.config_sections() if section.owner in hidden}
    bases = {spec.agent for spec in knowledge.knowledge_specs()} - CORE_AGENTS

    agents_of, sections_of = registry.registered_specialists, cfg.config_sections
    specs_of, spec_of = knowledge.knowledge_specs, knowledge.knowledge_spec

    def visible_spec(agent):
        if agent in bases:
            raise KnowledgeError(f"no knowledge base is declared for {agent} (hidden from core tests)")
        return spec_of(agent)

    _replace_everywhere(monkeypatch, agents_of, lambda: [s for s in agents_of() if s.name not in hidden])
    _replace_everywhere(monkeypatch, sections_of, lambda: [s for s in sections_of() if s.name not in sections])
    _replace_everywhere(monkeypatch, specs_of, lambda: [s for s in specs_of() if s.agent not in bases])
    _replace_everywhere(monkeypatch, spec_of, visible_spec)
    return hidden


def pytest_configure(config):
    config.addinivalue_line("markers", "track_agents: a core test that sees the agents, config sections and "
                                       "knowledge bases of the track packages (CR-1)")


@pytest.fixture(autouse=True)
def _core_tests_do_not_see_track_agents(request, monkeypatch):
    if Path(str(request.node.fspath)).parent == Path(TESTS) and not request.node.get_closest_marker("track_agents"):
        hide_track_agents(monkeypatch)
