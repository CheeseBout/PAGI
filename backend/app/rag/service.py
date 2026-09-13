"""Glue between the RAG pipeline and the agent runtime / tool layer.

Keeps mode resolution (SPEC §14.5) in one place:

    off    -> no retrieval, no tool
    always -> retrieve before every turn, inject into extra_system
    tool   -> expose rag_search only; agent decides
    auto   -> 12a: behave like `always` AND expose the tool (router is 12c)
"""

from __future__ import annotations

import structlog

from sqlmodel import select

from ..config import get_settings
from ..db.models import Agent, KbChunk, KbCollection, KbDocument, KbQueryLog
from ..db.session import SessionLocal
from .config import resolve_config
from .pipeline import RetrievalResult, retrieve

log = structlog.get_logger("pagi.rag.service")


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return (dt.isoformat() + "Z") if dt.tzinfo is None else dt.isoformat().replace("+00:00", "Z")


async def rag_for_messages(message_ids: list[str]) -> dict[str, dict]:
    """{message_id: {query_log_id, citations, no_context, reason}} for assistant
    messages that triggered a retrieval — used to re-attach citations to a
    reloaded conversation (Phase 12e)."""
    if not message_ids:
        return {}
    async with SessionLocal() as db:
        logs = list(
            (
                await db.exec(
                    select(KbQueryLog).where(KbQueryLog.message_id.in_(message_ids))
                )
            ).all()
        )
        if not logs:
            return {}
        chunk_ids = {cid for lg in logs for cid in (lg.picked_chunk_ids or [])}
        chunks = {
            c.id: c
            for c in (
                await db.exec(select(KbChunk).where(KbChunk.id.in_(chunk_ids)))
            ).all()
        } if chunk_ids else {}
        doc_ids = {c.document_id for c in chunks.values()}
        docs = {
            d.id: d
            for d in (
                await db.exec(select(KbDocument).where(KbDocument.id.in_(doc_ids)))
            ).all()
        } if doc_ids else {}

    out: dict[str, dict] = {}
    for lg in logs:
        picked = lg.picked_chunk_ids or []
        cites = []
        for cid in picked:
            c = chunks.get(cid)
            if not c:
                continue
            d = docs.get(c.document_id)
            cites.append(
                {
                    "chunk_id": c.id,
                    "document_id": c.document_id,
                    "title": d.title if d else "document",
                    "source_uri": d.source_uri if d else "",
                    "updated_at": _iso(d.updated_at) if d else None,
                }
            )
        reason = None
        if not picked:
            stages = lg.stages or []
            reason = "no_hits"
            for s in stages:
                if s.get("name") == "metadata_filter" and s.get("candidate_count") == 0:
                    reason = "metadata_filter_empty"
        out[lg.message_id] = {
            "query_log_id": lg.id,
            "citations": cites,
            "no_context": not picked,
            "reason": reason,
        }
    return out


def _agent_collection_ids(agent: Agent) -> list[str]:
    return [c for c in (agent.kb_collection_ids or []) if c]


def rag_globally_enabled() -> bool:
    return bool(get_settings().rag_enabled)


async def resolved_config(agent: Agent) -> dict:
    """Cascade: env -> (first) collection -> agent."""
    coll_cfg: dict = {}
    ids = _agent_collection_ids(agent)
    if ids:
        async with SessionLocal() as db:
            coll = await db.get(KbCollection, ids[0])
            if coll is not None:
                coll_cfg = coll.config or {}
    return resolve_config(collection=coll_cfg, agent=agent.rag_config or {})


async def mode_for(agent: Agent) -> str:
    """Effective RAG mode for this agent (``off`` if RAG is unusable)."""
    if not rag_globally_enabled() or not _agent_collection_ids(agent):
        return "off"
    cfg = await resolved_config(agent)
    return cfg.get("mode") or "auto"


def wants_pretrieval(mode: str) -> bool:
    return mode in ("always", "auto")


def wants_tool(mode: str) -> bool:
    return mode in ("tool", "auto")


async def retrieve_for_turn(
    agent: Agent,
    *,
    session_id: str,
    query: str,
    budget_tokens: int,
    message_id: str | None = None,
) -> RetrievalResult | None:
    ids = _agent_collection_ids(agent)
    if not ids or not query.strip():
        return None
    cfg = await resolved_config(agent)
    try:
        return await retrieve(
            query=query,
            collection_ids=ids,
            cfg=cfg,
            session_id=session_id,
            message_id=message_id,
            budget_tokens=budget_tokens,
            rerank_model=agent.model,
            rerank_provider=agent.provider,
        )
    except Exception as exc:  # pragma: no cover - retrieval must never kill a turn
        log.warning("retrieve_for_turn_failed", error=str(exc))
        return None


# agentic RAG: cap rag_search calls per turn (SPEC §14.2 max_retrieval_rounds).
_rounds: dict[str, int] = {}


def reset_rounds(session_id: str) -> None:
    _rounds.pop(session_id, None)


async def search_for_tool(
    agent: Agent,
    *,
    session_id: str,
    query: str,
    collection_ids: list[str] | None,
    top_k: int | None,
) -> dict:
    ids = [c for c in (collection_ids or _agent_collection_ids(agent)) if c]
    if not ids:
        return {"error": "no knowledge collections are attached to this agent", "chunks": []}
    cfg = await resolved_config(agent)
    max_rounds = int(cfg.get("max_retrieval_rounds") or 2)
    used = _rounds.get(session_id, 0)
    if used >= max_rounds:
        return {
            "error": f"max_retrieval_rounds ({max_rounds}) reached for this turn — "
            "answer from what you already retrieved",
            "chunks": [],
        }
    _rounds[session_id] = used + 1
    if top_k:
        cfg = {**cfg, "rerank_top_n": max(1, min(int(top_k), 20))}
    res = await retrieve(
        query=query,
        collection_ids=ids,
        cfg=cfg,
        session_id=session_id,
        budget_tokens=cfg.get("context_max_tokens") or 4000,
        rerank_model=agent.model,
        rerank_provider=agent.provider,
    )
    return {
        "chunks": res.chunks,
        "context": res.packed.text,
        "query_log_id": res.query_log_id,
        "no_context": res.no_context,
        "truncated": False,
    }
