"""Shared test setup (step M0).

Track tests live in ``tests/<track>/`` (for example ``tests/pydyna/``). This adds
``tests/`` to the import path so they can use ``from fake_model import ...`` like
the tests in this folder. Test file names must be unique across all folders
(pytest's default import mode): start them with the track name, for example
``tests/pydyna/test_pydyna_keyword.py``.
"""

import sys
from pathlib import Path

TESTS = str(Path(__file__).resolve().parent)
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)
