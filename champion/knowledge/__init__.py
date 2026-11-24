"""Knowledge layer (M1): read-only search in an agent's knowledge root.

Every knowledge-based agent (PyDyna, ANSA/META, PRIMER) has one root inside the
project folder, ``knowledge/<agent>/`` by default (``<section>.knowledge_root``):

* ``knowledge-base/``: the JSONL sources;
* ``vector_db/`` (and more folders named ``vector_db_<name>``): Chroma databases,
  each with a ``MANIFEST.json`` that says how it was built.

An agent declares its collections once (``registry.register_knowledge``) and gets
search and lookup tools from ``tools.make_search_tool`` / ``make_lookup_tool``;
it never opens Chroma itself. Modules:

* ``root``: where the root is; refused outside the project folder or through a link.
* ``manifest``: ``MANIFEST.json`` (model, dimension, distance, counts, file SHA-256).
* ``embedding``: the embedding model, loaded only from the local cache (offline).
* ``store``: the Chroma database opened in place, read only; a changed file stops it.
* ``search``: search and lookup with raw distances, score, source and chunk.
* ``tools``: tool factory (policy entry, guard, audit, output cap).
* ``builder``: builds a collection from the agent's records (``champion kb build``).
* ``status``: the checks of ``champion kb status`` and ``champion doctor``.
"""
