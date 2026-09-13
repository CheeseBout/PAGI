"""Vector store seam (SPEC §14 / PLAN §12a).

``sqlite_np`` (numpy brute-force over ``kb_chunks.embedding``) is the only
implementation for now; ``pgvector`` is a documented seam. Pick via
``RAG_VECTOR_STORE``.
"""

from __future__ import annotations

from functools import lru_cache

from ...config import get_settings
from .base import VectorHit, VectorStore


@lru_cache(maxsize=1)
def get_store() -> VectorStore:
    name = get_settings().rag_vector_store
    if name == "sqlite_np":
        from .sqlite_np import SqliteNumpyStore

        return SqliteNumpyStore()
    raise RuntimeError(f"unknown RAG_VECTOR_STORE: {name!r} (only 'sqlite_np' is implemented)")


__all__ = ["VectorHit", "VectorStore", "get_store"]
