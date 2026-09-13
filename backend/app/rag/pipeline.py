"""Retrieval pipeline (SPEC §14.4).

    transform -> [metadata filter] -> dense -> sparse -> fuse
              -> dedupe -> threshold -> rerank -> expand -> grade (CRAG) -> pack

12a dense-only; 12b sparse/fusion/rerank/parent-child/dedupe; 12c query transform
(hyde / multi_query / step_back / decompose / auto) unioned across the dense +
BM25 hits, and Corrective RAG (grade the context, then broaden the query or fall
back to web search). Every stage appends to ``stages`` (skipped ones too) so
``kb_query_logs`` and the Playground can pinpoint a bad answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog
from sqlmodel import select

from ..db.models import KbChunk, KbCollection, KbDocument, KbQueryLog
from ..db.session import SessionLocal
from . import crag as crag_mod
from . import query_transform
from .bm25 import get_sparse_store
from .config import resolve_config
from .embeddings import embed_query, embed_texts
from .fusion import fuse
from .packer import PackChunk, PackedContext, pack
from .rerank import RerankCand, rerank
from .store import get_store

log = structlog.get_logger("pagi.rag.pipeline")

_CANDIDATE_CAP = 60  # rows we hydrate for dedupe/rerank after fusion


@dataclass
class RetrievalResult:
    packed: PackedContext
    chunks: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    query_log_id: str | None = None
    no_context: bool = False
    reason: str | None = None
    total_ms: int = 0


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return (dt.isoformat() + "Z") if dt.tzinfo is None else dt.isoformat().replace("+00:00", "Z")


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _meta_match(filt: dict, chunk_meta: dict, doc_meta: dict) -> bool:
    for key, want in filt.items():
        have = chunk_meta.get(key, doc_meta.get(key))
        if isinstance(want, (list, tuple, set)):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


async def _allowed_by_metadata(
    collection_ids: list[str], filt: dict
) -> set[str] | None:
    if not filt:
        return None
    async with SessionLocal() as db:
        crows = list(
            (
                await db.exec(
                    select(KbChunk.id, KbChunk.document_id, KbChunk.meta).where(
                        KbChunk.collection_id.in_(collection_ids)
                    )
                )
            ).all()
        )
        doc_ids = {dcid for _, dcid, _ in crows}
        dmeta = {
            d.id: (d.meta or {})
            for d in (
                await db.exec(select(KbDocument).where(KbDocument.id.in_(doc_ids)))
            ).all()
        }
    return {
        cid
        for cid, dcid, cmeta in crows
        if _meta_match(filt, cmeta or {}, dmeta.get(dcid, {}))
    }


def _merge_max(acc: dict[str, float], hits: list[tuple[str, float]]) -> None:
    for cid, s in hits:
        if s > acc.get(cid, -1e9):
            acc[cid] = s


async def _gather(
    query_texts: list[str],
    cfg: dict,
    colls: list,
    model: str,
    allowed: set[str] | None,
    session_id: str | None,
    retrieval_mode: str,
    stages: list[dict],
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[str, float]], list[float]]:
    """Dense + BM25 over every transformed query, union keeping the max score,
    then fuse. Appends the dense/sparse/fuse stages."""
    dense_acc: dict[str, float] = {}
    sparse_acc: dict[str, float] = {}
    qvec0: list[float] = []

    ts = time.perf_counter()
    if retrieval_mode in ("dense", "hybrid"):
        vecs = await embed_texts(model, query_texts, session_id=session_id, kind="embedding")
        qvec0 = vecs[0] if vecs else []
        store = get_store()
        top_k = int(cfg.get("top_k_dense") or 50)
        for vec in vecs:
            for c in colls:
                _merge_max(
                    dense_acc,
                    [
                        (h.chunk_id, h.score)
                        for h in await store.query(c.id, vec, top_k, allowed_chunk_ids=allowed)
                    ],
                )
    dense_hits = sorted(dense_acc.items(), key=lambda t: t[1], reverse=True)
    stages.append(
        {
            "name": "dense",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(dense_hits),
            "skipped": retrieval_mode == "sparse",
            "top": [{"chunk_id": cid, "score": round(s, 4)} for cid, s in dense_hits[:10]],
        }
    )

    ts = time.perf_counter()
    if retrieval_mode in ("sparse", "hybrid"):
        sp = get_sparse_store()
        top_k = int(cfg.get("top_k_sparse") or 50)
        for qt in query_texts:
            for c in colls:
                _merge_max(
                    sparse_acc,
                    await sp.search(
                        c.id, qt, top_k,
                        k1=float(cfg.get("bm25_k1") or 1.5),
                        b=float(cfg.get("bm25_b") or 0.75),
                        vi_segmentation=bool(cfg.get("vi_segmentation", True)),
                        allowed_chunk_ids=allowed,
                    ),
                )
    sparse_hits = sorted(sparse_acc.items(), key=lambda t: t[1], reverse=True)
    stages.append(
        {
            "name": "sparse",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(sparse_hits),
            "skipped": retrieval_mode == "dense",
            "top": [{"chunk_id": cid, "score": round(s, 4)} for cid, s in sparse_hits[:10]],
        }
    )

    ts = time.perf_counter()
    fused = fuse(dense_hits, sparse_hits, cfg)
    stages.append(
        {
            "name": "fuse",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(fused),
            "skipped": not (dense_hits and sparse_hits),
            "method": cfg.get("fusion") or "rrf",
        }
    )
    return dense_hits, sparse_hits, fused, qvec0


async def retrieve(
    *,
    query: str,
    collection_ids: list[str],
    cfg: dict | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
    budget_tokens: int = 4000,
    persist_log: bool = True,
    rerank_model: str | None = None,
    rerank_provider: str | None = None,
) -> RetrievalResult:
    t0 = time.perf_counter()
    query = (query or "").strip()
    stages: list[dict] = []

    async with SessionLocal() as db:
        colls = list(
            (
                await db.exec(select(KbCollection).where(KbCollection.id.in_(collection_ids)))
            ).all()
        )
    if not query or not colls:
        return RetrievalResult(
            packed=PackedContext(text="", system_note=""),
            no_context=True,
            reason="empty_query" if not query else "no_collections",
        )

    cfg = cfg or resolve_config(collection=colls[0].config)
    models = {c.embedding_model for c in colls}
    if len(models) > 1:
        log.warning("mixed_embedding_models", models=sorted(models))
    model = colls[0].embedding_model
    coll_ids = [c.id for c in colls]

    retrieval_mode = cfg.get("retrieval_mode") or "hybrid"

    # ── stage: transform (SPEC §14.4 step 1) ──────────────────────
    ts = time.perf_counter()
    queries, tmode = await query_transform.transform(
        query, cfg, model=rerank_model, provider=rerank_provider
    )
    stages.append(
        {
            "name": "transform",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(queries),
            "skipped": tmode == "none",
            "mode": tmode,
            "queries": queries,
        }
    )

    # ── pre-filter by metadata (SPEC §14.4) ─────────────────────────
    ts = time.perf_counter()
    allowed = await _allowed_by_metadata(coll_ids, cfg.get("metadata_filter") or {})
    if allowed is not None:
        stages.append(
            {
                "name": "metadata_filter",
                "duration_ms": int((time.perf_counter() - ts) * 1000),
                "candidate_count": len(allowed),
            }
        )
        if not allowed:
            return await _empty(
                query, queries, cfg, stages, session_id, message_id, coll_ids, t0,
                persist_log, "metadata_filter_empty",
            )

    # ── dense + sparse + fuse (union over the transformed queries) ──
    dense_hits, sparse_hits, fused, qvec = await _gather(
        [q["text"] for q in queries], cfg, colls, model, allowed, session_id, retrieval_mode,
        stages,
    )
    if not fused:
        return await _empty(
            query, queries, cfg, stages, session_id, message_id, coll_ids, t0,
            persist_log, "no_hits",
        )

    # hydrate the top candidates once (needed for dedupe, rerank, expand)
    cand_ids = [cid for cid, _ in fused[:_CANDIDATE_CAP]]
    fused_score = {cid: s for cid, s in fused}
    async with SessionLocal() as db:
        rows = {
            r.id: r
            for r in (
                await db.exec(select(KbChunk).where(KbChunk.id.in_(cand_ids)))
            ).all()
        }
    ordered_rows = [rows[cid] for cid in cand_ids if cid in rows]

    # ── dedupe near-identical chunks ──────────────────────────────
    ts = time.perf_counter()
    dsim = float(cfg.get("dedupe_similarity") or 0.97)
    kept_rows: list[KbChunk] = []
    for r in ordered_rows:
        if any(_cos(r.embedding, k.embedding) >= dsim for k in kept_rows):
            continue
        kept_rows.append(r)
    stages.append(
        {
            "name": "dedupe",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(kept_rows),
            "removed": len(ordered_rows) - len(kept_rows),
        }
    )

    # ── threshold on fused score ─────────────────────────────────
    threshold = float(cfg.get("score_threshold") or 0.0)
    kept_rows = [r for r in kept_rows if fused_score.get(r.id, 0.0) >= threshold]
    if not kept_rows:
        return await _empty(
            query, queries, cfg, stages, session_id, message_id, coll_ids, t0,
            persist_log, "below_threshold",
        )

    # ── rerank ──────────────────────────────────────────────────
    ts = time.perf_counter()
    cands = [
        RerankCand(chunk_id=r.id, text=r.text, score=fused_score.get(r.id, 0.0),
                   embedding=r.embedding or None)
        for r in kept_rows
    ]
    ranked, mode_used = await rerank(
        query, cands, cfg,
        query_vec=qvec or None, model=rerank_model, provider=rerank_provider,
        session_id=session_id,
    )
    rank_score = {cid: s for cid, s in ranked}
    picked_rows = [rows[cid] for cid, _ in ranked if cid in rows]
    stages.append(
        {
            "name": "rerank",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(picked_rows),
            "mode": mode_used,
        }
    )
    if not picked_rows:
        return await _empty(
            query, queries, cfg, stages, session_id, message_id, coll_ids, t0,
            persist_log, "no_hits",
        )

    # ── expand: parent swap + adjacent merge (SPEC §14.4 step 6) ──
    ts = time.perf_counter()
    pack_chunks, expand_note = await _expand(picked_rows, rank_score)
    stages.append(
        {
            "name": "expand",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(pack_chunks),
            "detail": expand_note,
        }
    )

    # ── grade: Corrective RAG (SPEC §14.4 step 7) ───────────────
    ts = time.perf_counter()
    crag_verdict = None
    if cfg.get("crag_enabled"):
        verdict, cscore = await crag_mod.grade(
            query, [pc.text for pc in pack_chunks], cfg,
            model=rerank_model, provider=rerank_provider,
        )
        crag_verdict = verdict
        fb = cfg.get("crag_fallback") or "broaden"
        fb_detail = "n/a"
        if verdict == "insufficient" and fb == "web_search":
            web = await crag_mod.web_fallback(query)
            for i, wtext in enumerate(web):
                pack_chunks.append(
                    PackChunk(
                        chunk_id=f"web-{i}", document_id="web", title="web result",
                        source_uri="web", text=wtext, score=0.0, updated_at=None, ordinal=i,
                    )
                )
            fb_detail = f"web_search:+{len(web)}"
        elif verdict == "insufficient" and fb == "broaden" and (cfg.get("query_transform") or "none") != "multi_query":
            b_cfg = {**cfg, "query_transform": "multi_query", "score_threshold": 0.0}
            b_queries, _bm = await query_transform.transform(
                query, b_cfg, model=rerank_model, provider=rerank_provider
            )
            _dh, _sh, b_fused, _qv = await _gather(
                [q["text"] for q in b_queries], b_cfg, colls, model, allowed,
                session_id, retrieval_mode, stages,
            )
            extra_ids = [cid for cid, _ in b_fused[:_CANDIDATE_CAP] if cid not in {p.chunk_id for p in pack_chunks}]
            if extra_ids:
                async with SessionLocal() as db:
                    xrows = list((await db.exec(select(KbChunk).where(KbChunk.id.in_(extra_ids)))).all())
                    xdocs = {
                        d.id: d for d in (await db.exec(
                            select(KbDocument).where(KbDocument.id.in_([r.document_id for r in xrows]))
                        )).all()
                    }
                for r in xrows[: int(cfg.get("rerank_top_n") or 5)]:
                    pack_chunks.append(_pc_from_row(r, xdocs.get(r.document_id), 0.0))
            fb_detail = f"broaden:+{len(extra_ids)}"
        stages.append(
            {
                "name": "grade",
                "duration_ms": int((time.perf_counter() - ts) * 1000),
                "verdict": verdict,
                "score": cscore,
                "fallback": fb if verdict == "insufficient" else "none",
                "detail": fb_detail,
            }
        )
    else:
        stages.append({"name": "grade", "duration_ms": 0, "skipped": True})

    # ── pack ───────────────────────────────────────────────────
    ts = time.perf_counter()
    packed = pack(pack_chunks, cfg, budget_tokens=budget_tokens)
    stages.append(
        {
            "name": "pack",
            "duration_ms": int((time.perf_counter() - ts) * 1000),
            "candidate_count": len(packed.used_chunk_ids),
            "context_tokens": packed.token_count,
        }
    )

    total_ms = int((time.perf_counter() - t0) * 1000)
    used = set(packed.used_chunk_ids)
    chunks_out = [
        {
            "chunk_id": pc.chunk_id,
            "document_id": pc.document_id,
            "title": pc.title,
            "source_uri": pc.source_uri,
            "text": pc.text,
            "score": round(pc.score, 4),
        }
        for pc in pack_chunks
        if pc.chunk_id in used
    ]
    result = RetrievalResult(
        packed=packed,
        chunks=chunks_out,
        stages=stages,
        no_context=not packed.used_chunk_ids,
        total_ms=total_ms,
    )
    if persist_log:
        result.query_log_id = await _persist_log(
            query, queries, cfg, stages, packed.used_chunk_ids, packed.token_count,
            session_id, message_id, coll_ids, total_ms, crag_verdict,
        )
    return result


async def _expand(
    picked_rows: list[KbChunk], rank_score: dict[str, float]
) -> tuple[list[PackChunk], str]:
    """parent_child -> replace child with its parent (dedup by parent); otherwise
    merge chunks that are adjacent in the same document."""
    parent_ids = {r.parent_id for r in picked_rows if r.parent_id}
    doc_ids = {r.document_id for r in picked_rows}
    async with SessionLocal() as db:
        parents = {
            p.id: p
            for p in (
                await db.exec(select(KbChunk).where(KbChunk.id.in_(parent_ids)))
            ).all()
        } if parent_ids else {}
        docs = {
            d.id: d
            for d in (
                await db.exec(select(KbDocument).where(KbDocument.id.in_(doc_ids)))
            ).all()
        }

    note = "none"
    out: list[PackChunk] = []
    seen_parent: set[str] = set()
    if parent_ids:
        note = "parent_swap"
        for r in picked_rows:
            if r.parent_id and r.parent_id in parents:
                if r.parent_id in seen_parent:
                    continue
                seen_parent.add(r.parent_id)
                p = parents[r.parent_id]
                d = docs.get(p.document_id)
                out.append(
                    PackChunk(
                        chunk_id=r.id, document_id=p.document_id,
                        title=d.title if d else "document",
                        source_uri=d.source_uri if d else "",
                        text=p.text, score=rank_score.get(r.id, 0.0),
                        updated_at=_iso(d.updated_at) if d else None, ordinal=p.ordinal,
                    )
                )
            else:
                d = docs.get(r.document_id)
                out.append(_pc_from_row(r, d, rank_score.get(r.id, 0.0)))
        return out, note

    # adjacent merge within a document
    by_doc: dict[str, list[KbChunk]] = {}
    for r in picked_rows:
        by_doc.setdefault(r.document_id, []).append(r)
    merged_any = False
    for did, rs in by_doc.items():
        rs.sort(key=lambda x: x.ordinal)
        i = 0
        while i < len(rs):
            j = i
            text = rs[i].text
            best = rank_score.get(rs[i].id, 0.0)
            while j + 1 < len(rs) and rs[j + 1].ordinal == rs[j].ordinal + 1:
                j += 1
                text = text + "\n" + rs[j].text
                best = max(best, rank_score.get(rs[j].id, 0.0))
                merged_any = True
            d = docs.get(did)
            out.append(
                PackChunk(
                    chunk_id=rs[i].id, document_id=did,
                    title=d.title if d else "document",
                    source_uri=d.source_uri if d else "",
                    text=text, score=best,
                    updated_at=_iso(d.updated_at) if d else None, ordinal=rs[i].ordinal,
                )
            )
            i = j + 1
    out.sort(key=lambda pc: pc.score, reverse=True)
    return out, ("adjacent_merge" if merged_any else "none")


def _pc_from_row(r: KbChunk, d: KbDocument | None, score: float) -> PackChunk:
    return PackChunk(
        chunk_id=r.id, document_id=r.document_id,
        title=d.title if d else "document",
        source_uri=d.source_uri if d else "",
        text=r.text, score=score,
        updated_at=_iso(d.updated_at) if d else None, ordinal=r.ordinal,
    )


async def _empty(
    query, queries, cfg, stages, session_id, message_id, coll_ids, t0, persist_log, reason
) -> RetrievalResult:
    total_ms = int((time.perf_counter() - t0) * 1000)
    res = RetrievalResult(
        packed=PackedContext(text="", system_note=""),
        stages=stages, no_context=True, reason=reason, total_ms=total_ms,
    )
    if persist_log:
        res.query_log_id = await _persist_log(
            query, queries, cfg, stages, [], 0, session_id, message_id, coll_ids,
            total_ms, None,
        )
    return res


async def _persist_log(
    query, queries, cfg, stages, picked_ids, context_tokens,
    session_id, message_id, collection_ids, total_ms, crag_verdict,
) -> str | None:
    try:
        async with SessionLocal() as db:
            row = KbQueryLog(
                session_id=session_id,
                message_id=message_id,
                collection_ids=list(collection_ids),
                query_raw=query,
                queries_used=queries,
                config_snapshot=cfg,
                stages=stages,
                picked_chunk_ids=list(picked_ids),
                context_tokens=context_tokens,
                crag_verdict=crag_verdict,
                total_ms=total_ms,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    except Exception:  # pragma: no cover - never fail a turn over a log row
        log.warning("query_log_write_failed")
        return None


async def purge_old_query_logs(retention_days: int) -> int:
    from datetime import datetime, timedelta, timezone

    from sqlmodel import delete as sqldelete

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
    async with SessionLocal() as db:
        rows = (
            await db.exec(select(KbQueryLog.id).where(KbQueryLog.created_at < cutoff))
        ).all()
        if rows:
            await db.exec(sqldelete(KbQueryLog).where(KbQueryLog.created_at < cutoff))
            await db.commit()
    return len(rows)
