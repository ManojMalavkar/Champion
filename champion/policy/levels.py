"""Access levels: presets of the per-folder access list (step 4e).

=====  ==========================================  ==============  ===============
Level  Meaning                                     Working folder  Everything else
=====  ==========================================  ==============  ===============
1.1    Working folder, read only                   read            none
1.2    Working folder, read and edit               edit            none
2.1    Working folder and outside, read only       read            read
2.2    Working folder and outside, read and edit   edit            edit
custom Converted from ``policy.custom``            any             any
=====  ==========================================  ==============  ===============

On top of a preset the user can add any number of folders with their own
right (``/access``, see ``access.py``). Some paths are blocked at every level
(``sensitive.py``). The level lives in the run context, so the model can
neither see nor change it; only the user can.

This module keeps the step 2 names (``AccessLevel``, ``BUILT_IN``,
``get_level``) for the code and tests that use them.
"""

from __future__ import annotations

from champion.policy.access import BUILT_IN, AccessLevel, preset_level

__all__ = ["AccessLevel", "BUILT_IN", "get_level"]


def get_level(name: str, config) -> AccessLevel:
    """Return the preset called ``name``; ``custom`` is converted from ``policy.custom``."""
    return preset_level(name, config)
