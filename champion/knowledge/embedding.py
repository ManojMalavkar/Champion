"""Embedding model of the knowledge layer (M1).

A query must be embedded with the model its collection was built with (the manifest
says which; ``champion kb status`` checks it). The model comes from the local Hugging
Face cache only: with ``knowledge.offline: true`` (default) ``HF_HUB_OFFLINE=1`` and
``TRANSFORMERS_OFFLINE=1`` are set before sentence-transformers is imported, so nothing
is downloaded and no request leaves the machine. Vectors are not normalised, like
Chroma's ``SentenceTransformerEmbeddingFunction`` that built the existing databases.

One embedder per model per Champion run (loaded on first use, then shared). Tests put
a fake embedder in with ``set_embedder``.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Protocol, Sequence

from champion.knowledge.root import KnowledgeError


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _offline_env() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def download_hint(model: str) -> str:
    return (f"download it once where internet is allowed: python -c \"from sentence_transformers import "
            f"SentenceTransformer; SentenceTransformer('{model}')\" and copy ~/.cache/huggingface to this machine")


class SentenceTransformerEmbedder:
    def __init__(self, model: str, offline: bool = True):
        self.model = model
        self.offline = offline
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            if self.offline:
                _offline_env()
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise KnowledgeError("search needs the optional packages chromadb and sentence-transformers "
                                     f"(pip install -e '.[knowledge]'); {exc}") from None
            try:
                try:
                    model = SentenceTransformer(self.model, device="cpu", local_files_only=self.offline)
                except TypeError:  # older sentence-transformers: the environment variables do the same
                    model = SentenceTransformer(self.model, device="cpu")
            except Exception as exc:  # OSError, HF errors: the model is not in the cache
                where = "in the local Hugging Face cache" if self.offline else "available"
                raise KnowledgeError(f"embedding model {self.model} is not {where} ({type(exc).__name__}); "
                                     + download_hint(self.model)) from None
            self._model = model
            return model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(list(texts), convert_to_numpy=True, normalize_embeddings=False,
                               show_progress_bar=False)
        return [[float(x) for x in row] for row in vectors]


_EMBEDDERS: dict[str, Embedder] = {}
_LOCK = threading.Lock()


def embedder_for(model: str, offline: bool = True) -> Embedder:
    with _LOCK:
        if model not in _EMBEDDERS:
            _EMBEDDERS[model] = SentenceTransformerEmbedder(model, offline)
        return _EMBEDDERS[model]


def set_embedder(model: str, embedder: Embedder | None) -> None:
    """Use another embedder for ``model`` (tests); ``None`` removes it."""
    with _LOCK:
        if embedder is None:
            _EMBEDDERS.pop(model, None)
        else:
            _EMBEDDERS[model] = embedder


def model_in_cache(model: str) -> bool | None:
    """Whether the model's files are in the local Hugging Face cache (``None``: cannot tell)."""
    if model in _EMBEDDERS and not isinstance(_EMBEDDERS[model], SentenceTransformerEmbedder):
        return True  # a test embedder
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return None
    for name in (model, f"sentence-transformers/{model}") if "/" not in model else (model,):
        try:
            if isinstance(try_to_load_from_cache(name, "config.json"), str):
                return True
        except Exception:  # bad repo id and similar
            continue
    return False
