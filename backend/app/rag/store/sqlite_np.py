"""numpy brute-force cosine over ``kb_chunks.embedding`` (SPEC §14.10).

Single-tenant scale: load a collection's vectors into one float32 matrix, cache
it, and do a matmul per query. Fast enough well past 100k chunks; past
``RAG_MAX_CHUNKS_PER_COLLECTION`` the answer is pgvector, not a bigger matrix.

The authoritative data is the ``kb_chunks`` rows — this store is a read cache.
``invalidate`` is called by the ingest path after it rewrites a document's
chunks.
"""

from __future__ import annotations

import asyncio

import numpy as np
import structlog
from sqlmodel import select

from ...db.models import KbChunk
from ...db.session import SessionLocal
from .base import VectorHit

log = structlog.get_logger("pagi.rag.store")


class _Index:
    __slots__ = ("ids", "mat", "dim")

    def __init__(self, ids: list[str], mat: np.ndarray) -> None:
        self.ids = ids
        self.mat = mat  # (n, dim), L2-normalised, float32
        self.dim = mat.shape[1] if mat.size else 0


class SqliteNumpyStore:
    def __init__(self) -> None:
        self._cache: dict[str, _Index] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_loop: object | None = None

    def invalidate(self, collection_id: str) -> None:
        self._cache.pop(collection_id, None)

    def _lock(self, collection_id: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._locks_loop is not loop:  # loop changed (tests) — drop stale locks
            self._locks = {}
            self._locks_loop = loop
        return self._locks.setdefault(collection_id, asyncio.Lock())

    async def _index(self, collection_id: str) -> _Index:
        hit = self._cache.get(collection_id)
        if hit is not None:
            return hit
        async with self._lock(collection_id):
            hit = self._cache.get(collection_id)
            if hit is not None:
                return hit
            async with SessionLocal() as db:
                rows = list(
                    (
                        await db.exec(
                            select(KbChunk.id, KbChunk.embedding).where(
                                KbChunk.collection_id == collection_id
                            )
                        )
                    ).all()
                )
            ids: list[str] = []
            vecs: list[list[float]] = []
            for cid, emb in rows:
                if emb:
                    ids.append(cid)
                    vecs.append(emb)
            if vecs:
                mat = np.asarray(vecs, dtype=np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                mat = mat / norms
            else:
                mat = np.zeros((0, 0), dtype=np.float32)
            idx = _Index(ids, mat)
            self._cache[collection_id] = idx
            return idx

    async def query(
        self,
        collection_id: str,
        query_vec: list[float],
        top_k: int,
        *,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[VectorHit]:
        idx = await self._index(collection_id)
        if idx.dim == 0 or not query_vec:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        if q.shape[0] != idx.dim:
            log.warning(
                "vector_dim_mismatch", collection_id=collection_id, q=q.shape[0], index=idx.dim
            )
            return []
        n = np.linalg.norm(q)
        if n == 0:
            return []
        q = q / n

        sims = idx.mat @ q  # (n,)

        if allowed_chunk_ids is not None:
            mask = np.array([cid in allowed_chunk_ids for cid in idx.ids], dtype=bool)
            if not mask.any():
                return []
            sims = np.where(mask, sims, -np.inf)

        k = min(top_k, len(idx.ids))
        if k <= 0:
            return []
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        hits: list[VectorHit] = []
        for i in top:
            score = float(sims[i])
            if score == -np.inf:
                continue
            hits.append(VectorHit(chunk_id=idx.ids[i], score=score))
        return hits
