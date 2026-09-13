"""Embedding client for RAG (SPEC §14.3).

- batches via ``litellm.aembedding``
- in-process cache keyed by (model, sha256(text)) so re-ingesting unchanged
  content costs nothing
- retries with exponential backoff; on exhaustion raises so the caller marks the
  document ``error`` (never writes half a document)
- every call records a ``Trace(kind="embedding")`` so the Usage tab accounts for
  RAG spend
"""

from __future__ import annotations

import asyncio
import hashlib

import structlog

from ..config import get_settings
from ..db.models import Trace
from ..db.session import SessionLocal
from ..observability.tracing import estimate_cost

log = structlog.get_logger("pagi.rag.embed")

_CACHE: dict[str, list[float]] = {}
_CACHE_MAX = 50_000
_MAX_INPUT_CHARS = 8_000


class EmbeddingError(RuntimeError):
    pass


def _key(model: str, text: str) -> str:
    return model + ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _one_batch(model: str, inputs: list[str], *, retries: int) -> list[list[float]]:
    import litellm

    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            resp = await litellm.aembedding(model=model, input=inputs)
            data = sorted(resp["data"], key=lambda d: d.get("index", 0))
            return [list(d["embedding"]) for d in data]
        except Exception as exc:  # pragma: no cover - external
            last_exc = exc
            log.warning("embed_batch_failed", attempt=attempt, error=str(exc))
            await asyncio.sleep(delay)
            delay *= 2
    raise EmbeddingError(f"embedding failed after {retries} attempts: {last_exc}")


async def embed_texts(
    model: str,
    texts: list[str],
    *,
    batch_size: int | None = None,
    session_id: str | None = None,
    kind: str = "embedding",
) -> list[list[float]]:
    """Embed ``texts`` (order preserved). Cached entries skip the network."""
    settings = get_settings()
    bs = batch_size or settings.rag_embed_batch_size
    retries = settings.rag_embed_max_retries

    out: list[list[float] | None] = [None] * len(texts)
    todo_idx: list[int] = []
    todo_txt: list[str] = []
    for i, raw in enumerate(texts):
        t = (raw or "").strip()[:_MAX_INPUT_CHARS] or " "
        cached = _CACHE.get(_key(model, t))
        if cached is not None:
            out[i] = cached
        else:
            todo_idx.append(i)
            todo_txt.append(t)

    for start in range(0, len(todo_txt), bs):
        chunk_idx = todo_idx[start : start + bs]
        chunk_txt = todo_txt[start : start + bs]
        vectors = await _one_batch(model, chunk_txt, retries=retries)
        if len(vectors) != len(chunk_txt):
            raise EmbeddingError("embedding provider returned a mismatched count")
        for j, vec in zip(chunk_idx, vectors):
            out[j] = vec
        for t, vec in zip(chunk_txt, vectors):
            if len(_CACHE) < _CACHE_MAX:
                _CACHE[_key(model, t)] = vec

    if todo_txt:
        await _record_trace(model, sum(len(t) for t in todo_txt), session_id, kind)

    result: list[list[float]] = []
    for v in out:
        if v is None:  # pragma: no cover - defensive
            raise EmbeddingError("embedding gap after batching")
        result.append(v)
    return result


async def embed_query(model: str, text: str, *, session_id: str | None = None) -> list[float]:
    vecs = await embed_texts(model, [text], session_id=session_id, kind="embedding")
    return vecs[0]


async def _record_trace(model: str, total_chars: int, session_id: str | None, kind: str) -> None:
    approx_tokens = max(1, total_chars // 4)
    try:
        cost = estimate_cost(model, approx_tokens, 0)
    except Exception:  # pragma: no cover - defensive
        cost = None
    try:
        async with SessionLocal() as db:
            db.add(
                Trace(
                    session_id=session_id,
                    provider="",
                    model=model,
                    latency_ms=0,
                    tokens_in=approx_tokens,
                    tokens_out=0,
                    cost_usd=cost,
                    kind=kind,
                )
            )
            await db.commit()
    except Exception:  # pragma: no cover - never fail ingest over bookkeeping
        log.warning("embed_trace_write_failed", model=model)


def clear_cache() -> None:
    _CACHE.clear()
