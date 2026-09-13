"""Okapi BM25 sparse retrieval over ``kb_chunks.text`` (SPEC §14.2 group 2).

Pure-Python, no dependency. Like the vector store this is a *read cache* over the
authoritative ``kb_chunks`` rows: one index per collection, built lazily, dropped
by ``invalidate`` after an ingest / delete.

Tokenisation keeps IDs / codes intact (``ABC-123``, ``v2.5``, ``SKU_9981``) so
BM25 covers the exact-match queries dense retrieval is blind to. When
``vi_segmentation`` is on and ``underthesea`` is importable, Vietnamese text is
word-segmented first; otherwise it degrades to the regex tokeniser with a warning.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter

import structlog
from sqlmodel import select

from ..db.models import KbChunk
from ..db.session import SessionLocal

log = structlog.get_logger("pagi.rag.bm25")

_TOKEN_RE = re.compile(r"[^\W_]+(?:[-_./][^\W_]+)*", re.UNICODE)

_vi_warned = False


def _vi_segment(text: str) -> list[str] | None:
    try:
        from underthesea import word_tokenize
    except Exception:  # pragma: no cover - optional extra
        global _vi_warned
        if not _vi_warned:
            log.info("underthesea_missing", detail="vi_segmentation falls back to regex")
            _vi_warned = True
        return None
    return [w.replace(" ", "_").lower() for w in word_tokenize(text) if w.strip()]


def tokenize(text: str, *, vi_segmentation: bool = False) -> list[str]:
    text = text or ""
    if vi_segmentation:
        segmented = _vi_segment(text)
        if segmented is not None:
            return segmented
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class Bm25Index:
    """Classic Okapi BM25. ``k1`` / ``b`` are passed per query (they're config,
    not fixed at build time)."""

    __slots__ = ("ids", "_docs", "_df", "_idf", "_avgdl", "_n")

    def __init__(self, ids: list[str], token_lists: list[list[str]]) -> None:
        self.ids = ids
        self._docs: list[Counter[str]] = [Counter(toks) for toks in token_lists]
        self._n = len(token_lists)
        self._df: Counter[str] = Counter()
        for toks in token_lists:
            for term in set(toks):
                self._df[term] += 1
        self._avgdl = (sum(len(t) for t in token_lists) / self._n) if self._n else 0.0
        self._idf: dict[str, float] = {
            term: math.log(1 + (self._n - df + 0.5) / (df + 0.5)) for term, df in self._df.items()
        }

    def search(
        self, query_tokens: list[str], top_k: int, *, k1: float = 1.5, b: float = 0.75,
        allowed: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not self._n or not query_tokens:
            return []
        q = [t for t in query_tokens if t in self._idf]
        if not q:
            return []
        scores: list[tuple[str, float]] = []
        for i, doc in enumerate(self._docs):
            cid = self.ids[i]
            if allowed is not None and cid not in allowed:
                continue
            dl = sum(doc.values()) or 1
            s = 0.0
            for term in q:
                tf = doc.get(term, 0)
                if not tf:
                    continue
                idf = self._idf[term]
                s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / (self._avgdl or 1)))
            if s > 0:
                scores.append((cid, s))
        scores.sort(key=lambda t: t[1], reverse=True)
        return scores[:top_k]


class _Sparse:
    def __init__(self) -> None:
        self._cache: dict[str, Bm25Index] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_loop: object | None = None

    def invalidate(self, collection_id: str) -> None:
        self._cache.pop(collection_id, None)

    def _lock(self, collection_id: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._locks_loop is not loop:
            self._locks = {}
            self._locks_loop = loop
        return self._locks.setdefault(collection_id, asyncio.Lock())

    async def _index(self, collection_id: str, *, vi_segmentation: bool) -> Bm25Index:
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
                            select(KbChunk.id, KbChunk.text, KbChunk.embedding).where(
                                KbChunk.collection_id == collection_id
                            )
                        )
                    ).all()
                )
            ids: list[str] = []
            toks: list[list[str]] = []
            for cid, text, emb in rows:
                # skip parent rows (no embedding) — they aren't independently retrievable
                if not emb:
                    continue
                ids.append(cid)
                toks.append(tokenize(text, vi_segmentation=vi_segmentation))
            idx = Bm25Index(ids, toks)
            self._cache[collection_id] = idx
            return idx

    async def search(
        self,
        collection_id: str,
        query: str,
        top_k: int,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        vi_segmentation: bool = False,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        idx = await self._index(collection_id, vi_segmentation=vi_segmentation)
        q = tokenize(query, vi_segmentation=vi_segmentation)
        return idx.search(q, top_k, k1=k1, b=b, allowed=allowed_chunk_ids)


_STORE = _Sparse()


def get_sparse_store() -> _Sparse:
    return _STORE
