"""Semantic recall over past conversation turns (Wave 3d).

Inactive unless ``EMBEDDING_MODEL`` is set. Embeddings are stored as JSON float
arrays in ``memory_chunks``; nearest-neighbour search runs in Python, which is
fine at single-tenant scale. Indexing is fire-and-forget so it never slows a
turn; retrieval failures degrade to "no extra context".
"""

from __future__ import annotations

import math

import structlog
from sqlmodel import select

from ..config import get_settings
from ..db.models import MemoryChunk
from ..db.session import SessionLocal

log = structlog.get_logger("pagi.memory")

_MIN_INDEX_CHARS = 40
_SIM_THRESHOLD = 0.75


def enabled() -> bool:
    return bool(get_settings().embedding_model)


async def _embed(text: str) -> list[float] | None:
    settings = get_settings()
    if not settings.embedding_model or not text.strip():
        return None
    try:
        import litellm

        resp = await litellm.aembedding(model=settings.embedding_model, input=[text[:8000]])
        return list(resp["data"][0]["embedding"])
    except Exception as exc:  # pragma: no cover - external
        log.warning("embed_failed", error=str(exc))
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0


async def index_message(
    session_id: str, user_id: str, message_id: str | None, role: str, text: str
) -> None:
    if not enabled():
        return
    text = (text or "").strip()
    if len(text) < _MIN_INDEX_CHARS:
        return
    emb = await _embed(text)
    if emb is None:
        return
    try:
        async with SessionLocal() as db:
            db.add(
                MemoryChunk(
                    session_id=session_id,
                    user_id=user_id,
                    message_id=message_id,
                    role=role,
                    text=text[:4000],
                    embedding=emb,
                )
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("memory_index_failed", error=str(exc))


async def retrieve(user_id: str, session_id: str, query: str) -> str | None:
    if not enabled() or not (query or "").strip():
        return None
    settings = get_settings()
    qemb = await _embed(query)
    if qemb is None:
        return None
    try:
        async with SessionLocal() as db:
            stmt = select(MemoryChunk)
            if settings.memory_cross_session:
                stmt = stmt.where(MemoryChunk.user_id == user_id)
            else:
                stmt = stmt.where(MemoryChunk.session_id == session_id)
            rows = list((await db.exec(stmt)).all())
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("memory_retrieve_failed", error=str(exc))
        return None

    scored = sorted(
        ((_cosine(qemb, r.embedding), r) for r in rows), key=lambda t: t[0], reverse=True
    )
    picked = [r for s, r in scored[: settings.memory_top_k] if s >= _SIM_THRESHOLD]
    if not picked:
        return None
    lines = [f"- ({r.role}) {r.text[:500]}" for r in picked]
    return "Possibly relevant context from earlier conversations:\n" + "\n".join(lines)
