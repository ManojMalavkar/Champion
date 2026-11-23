"""Champion memory (track T8). Library only; the chat uses it after core step M6.

Layers built first (docs/tracks/memory.md):

* L0 ``runtime_state`` - execution state of the running request (``.champion/runtime/*.json``)
* L1 ``working`` - working memory of the current conversation and selection of earlier turns
* L2 ``summaries`` - structured summaries of completed conversation parts
* L3 ``preferences`` - user and operating preferences

``separation`` keeps preferences, engineering facts, requirements and results apart;
``storage`` has the shared file helpers (atomic JSON, mode 600, secret filter).
"""

from champion.memory.storage import MemoryRefused

__all__ = ["MemoryRefused"]
