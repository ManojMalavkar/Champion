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
"""

import sys
from pathlib import Path

import pytest

TESTS = str(Path(__file__).resolve().parent)
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

CORE_AGENTS = frozenset({"coder", "linux_coder"})  # agents of champion/specialists/*.py


def hide_track_agents(monkeypatch) -> set[str]:
    """Leave the agents registered now, other than the core agents, out of ``registered_specialists``."""
    from champion.specialists import registry

    registry.load_builtin_specialists()
    hidden = {spec.name for spec in registry.registered_specialists()} - CORE_AGENTS
    original = registry.registered_specialists
    monkeypatch.setattr(registry, "registered_specialists",
                        lambda: [spec for spec in original() if spec.name not in hidden])
    return hidden


@pytest.fixture(autouse=True)
def _core_tests_do_not_see_track_agents(request, monkeypatch):
    if Path(str(request.node.fspath)).parent == Path(TESTS):
        hide_track_agents(monkeypatch)
