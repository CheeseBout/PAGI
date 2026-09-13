"""Knowledge Base / RAG API (SPEC §2.9, Phase 12).

All endpoints here are user actions via the UI. Ingesting / deleting documents is
never exposed to the agent — it only gets the read-only ``rag_search`` tool.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings
from ..db.models import (
    Agent,
    KbChunk,
    KbCollection,
    KbDocument,
    KbEvalCase,
    KbEvalRun,
    KbQueryLog,
    User,
)
from ..rag import docstore
from ..rag import ingest as ingest_mod
from ..rag.config import RagConfig, resolve_config
from ..rag.pipeline import retrieve as pipeline_retrieve
from ..schemas import (
    KbCollectionCreate,
    KbCollectionPatch,
    KbDocFromText,
    KbDocFromUrl,
    KbEvalGenerate,
    KbEvalRunCreate,
    KbReingest,
    KbSearchRequest,
)
from .deps import APIError, get_current_user, get_db
from .serializers import (
    kb_chunk_out,
    kb_collection_out,
    kb_document_out,
    kb_eval_case_out,
    kb_eval_run_out,
    kb_query_log_out,
)

router = APIRouter(prefix="/api/kb", tags=["rag"])

_ALLOWED_CT = {
    "application/pdf",
    "application/json",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/html",
    "application/xhtml+xml",
}
_ALLOWED_EXT = {"pdf", "json", "txt", "text", "md", "markdown", "html", "htm"}


def _validate_config(cfg: dict) -> dict:
    try:
        RagConfig.model_validate(cfg or {})
    except ValidationError as exc:
        raise APIError(422, "invalid_config", f"invalid RagConfig: {exc.errors()}")
    return cfg or {}


def _schedule_ingest(doc_id: str) -> None:
    asyncio.create_task(ingest_mod.run(doc_id))


# ── collections ───────────────────────────────────────────────────────
@router.get("/collections")
async def list_collections(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    rows = (await db.exec(select(KbCollection).order_by(KbCollection.created_at))).all()
    return [kb_collection_out(c) for c in rows]


@router.post("/collections", status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: KbCollectionCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not get_settings().rag_enabled:
        raise APIError(403, "rag_disabled", "RAG is disabled (RAG_ENABLED=false)")
    name = body.name.strip()
    if not name:
        raise APIError(422, "invalid_name", "name is required")
    dup = (await db.exec(select(KbCollection).where(KbCollection.name == name))).first()
    if dup is not None:
        raise APIError(409, "duplicate_name", "a collection with that name already exists")
    _validate_config(body.config)
    coll = KbCollection(
        name=name,
        description=body.description or "",
        config=body.config or {},
        embedding_model=body.embedding_model or get_settings().rag_embedding_model,
    )
    db.add(coll)
    await db.commit()
    await db.refresh(coll)
    return kb_collection_out(coll)


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    out = kb_collection_out(coll)
    out["config_resolved"] = resolve_config(collection=coll.config)
    return out


@router.patch("/collections/{collection_id}")
async def patch_collection(
    collection_id: str,
    body: KbCollectionPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    data = body.model_dump(exclude_unset=True)
    if "config" in data:
        _validate_config(data["config"])
        coll.config = data["config"] or {}
    if "name" in data and data["name"]:
        other = (
            await db.exec(
                select(KbCollection).where(
                    KbCollection.name == data["name"].strip(), KbCollection.id != collection_id
                )
            )
        ).first()
        if other is not None:
            raise APIError(409, "duplicate_name", "a collection with that name already exists")
        coll.name = data["name"].strip()
    if "description" in data:
        coll.description = data["description"] or ""
    db.add(coll)
    await db.commit()
    await db.refresh(coll)
    return kb_collection_out(coll)


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    agents = (await db.exec(select(Agent))).all()
    if any(collection_id in (a.kb_collection_ids or []) for a in agents):
        raise APIError(409, "collection_in_use", "an agent references this collection")

    docs = (
        await db.exec(select(KbDocument).where(KbDocument.collection_id == collection_id))
    ).all()
    for d in docs:
        docstore.delete(d.id)
    from sqlmodel import delete as sqldelete

    await db.exec(sqldelete(KbChunk).where(KbChunk.collection_id == collection_id))
    await db.exec(sqldelete(KbDocument).where(KbDocument.collection_id == collection_id))
    await db.delete(coll)
    await db.commit()
    ingest_mod.invalidate_indexes(collection_id)


# ── documents ─────────────────────────────────────────────────────────
@router.get("/collections/{collection_id}/documents")
async def list_documents(
    collection_id: str,
    doc_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = (
        select(KbDocument)
        .where(KbDocument.collection_id == collection_id)
        .order_by(KbDocument.created_at.desc())
        .limit(limit)
    )
    if doc_status:
        stmt = stmt.where(KbDocument.status == doc_status)
    return [kb_document_out(d) for d in (await db.exec(stmt)).all()]


async def _new_document(
    db: AsyncSession, coll: KbCollection, *, source_type: str, source_uri: str, title: str,
    meta: dict,
) -> KbDocument:
    doc = KbDocument(
        collection_id=coll.id,
        source_type=source_type,
        source_uri=source_uri,
        title=title[:512] or source_uri[:512] or "document",
        meta=meta or {},
        status="pending",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@router.post("/collections/{collection_id}/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    collection_id: str,
    file: UploadFile = File(...),
    meta: str = Form(default="{}"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")

    ct = (file.content_type or "").split(";")[0].strip().lower()
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ct not in _ALLOWED_CT and ext not in _ALLOWED_EXT:
        raise APIError(400, "unsupported_type", f"unsupported type: {file.content_type or ext}")

    data = await file.read()
    if not data:
        raise APIError(400, "empty_file", "file is empty")
    if len(data) > get_settings().rag_max_doc_mb * 1024 * 1024:
        raise APIError(400, "too_large", f"file exceeds {get_settings().rag_max_doc_mb} MB")

    try:
        meta_obj = json.loads(meta or "{}")
    except ValueError:
        meta_obj = {}
    meta_obj["content_type"] = file.content_type or ""

    doc = await _new_document(
        db, coll, source_type="upload", source_uri=file.filename or "upload",
        title=(file.filename or "upload").rsplit(".", 1)[0], meta=meta_obj,
    )
    docstore.save(doc.id, data, ext=ext)
    _schedule_ingest(doc.id)
    return kb_document_out(doc)


@router.post(
    "/collections/{collection_id}/documents/from-url", status_code=status.HTTP_202_ACCEPTED
)
async def add_document_from_url(
    collection_id: str,
    body: KbDocFromUrl,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    from ..core.security import SSRFError, ssrf_guard

    try:
        ssrf_guard(body.url)
    except SSRFError as exc:
        raise APIError(400, "blocked_url", str(exc))
    doc = await _new_document(
        db, coll, source_type="url", source_uri=body.url,
        title=body.title or body.url, meta=body.meta,
    )
    _schedule_ingest(doc.id)
    return kb_document_out(doc)


@router.post(
    "/collections/{collection_id}/documents/from-text", status_code=status.HTTP_202_ACCEPTED
)
async def add_document_from_text(
    collection_id: str,
    body: KbDocFromText,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    if not body.text.strip():
        raise APIError(422, "empty_text", "text is required")
    meta = {**(body.meta or {}), "content_type": "text/plain"}
    doc = await _new_document(
        db, coll, source_type="text", source_uri=body.title, title=body.title, meta=meta,
    )
    docstore.save(doc.id, body.text.encode("utf-8"), ext="txt")
    _schedule_ingest(doc.id)
    return kb_document_out(doc)


@router.post("/documents/{doc_id}/reingest", status_code=status.HTTP_202_ACCEPTED)
async def reingest_document(
    doc_id: str,
    body: KbReingest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    doc = await db.get(KbDocument, doc_id)
    if doc is None:
        raise APIError(404, "not_found", "document not found")
    if body.force:
        doc.content_hash = ""  # force a full re-embed
    doc.status = "pending"
    doc.error = None
    db.add(doc)
    await db.commit()
    _schedule_ingest(doc.id)
    return kb_document_out(doc)


@router.get("/documents/{doc_id}/chunks")
async def list_chunks(
    doc_id: str,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    rows = (
        await db.exec(
            select(KbChunk)
            .where(KbChunk.document_id == doc_id)
            .order_by(KbChunk.ordinal)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [kb_chunk_out(c) for c in rows]


@router.get("/chunks/{chunk_id}")
async def get_chunk(
    chunk_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    """One chunk + its document — for a citation click-through (Phase 12e)."""
    c = await db.get(KbChunk, chunk_id)
    if c is None:
        raise APIError(404, "not_found", "chunk not found")
    d = await db.get(KbDocument, c.document_id)
    out = kb_chunk_out(c)
    out["document"] = (
        {"id": d.id, "title": d.title, "source_uri": d.source_uri, "source_type": d.source_type}
        if d
        else None
    )
    return out


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    doc = await db.get(KbDocument, doc_id)
    if doc is None:
        raise APIError(404, "not_found", "document not found")
    from sqlmodel import delete as sqldelete

    collection_id = doc.collection_id
    await db.exec(sqldelete(KbChunk).where(KbChunk.document_id == doc_id))
    await db.delete(doc)
    await db.commit()
    docstore.delete(doc_id)
    await ingest_mod.recount_collection(collection_id)
    ingest_mod.invalidate_indexes(collection_id)


# ── retrieval / playground ───────────────────────────────────────────
@router.post("/search")
async def search(
    body: KbSearchRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not body.collection_ids:
        raise APIError(422, "no_collections", "collection_ids is required")
    colls = (
        await db.exec(select(KbCollection).where(KbCollection.id.in_(body.collection_ids)))
    ).all()
    if not colls:
        raise APIError(404, "not_found", "no such collection")
    if body.config is not None:
        _validate_config(body.config)
    cfg = resolve_config(collection=colls[0].config, request=body.config or {})
    res = await pipeline_retrieve(
        query=body.query,
        collection_ids=[c.id for c in colls],
        cfg=cfg,
        budget_tokens=cfg.get("context_max_tokens") or 4000,
        # Playground has no agent — fall back to SUMMARY_MODEL for llm rerank.
        rerank_model=get_settings().summary_model,
    )
    out = {
        "chunks": res.chunks,
        "context": res.packed.text,
        "query_log_id": res.query_log_id,
        "no_context": res.no_context,
        "reason": res.reason,
        "total_ms": res.total_ms,
        "citations": res.packed.citations,
    }
    if body.explain:
        out["stages"] = res.stages
        out["config_resolved"] = cfg
    return out


@router.get("/query-logs")
async def list_query_logs(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(KbQueryLog).order_by(KbQueryLog.created_at.desc()).limit(limit)
    if session_id:
        stmt = stmt.where(KbQueryLog.session_id == session_id)
    return [kb_query_log_out(q) for q in (await db.exec(stmt)).all()]


@router.get("/query-logs/{log_id}")
async def get_query_log(
    log_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    row = await db.get(KbQueryLog, log_id)
    if row is None:
        raise APIError(404, "not_found", "query log not found")
    return kb_query_log_out(row)


# ── evaluation (Phase 12f) ───────────────────────────────────────────
@router.post("/collections/{collection_id}/eval/generate", status_code=status.HTTP_202_ACCEPTED)
async def eval_generate(
    collection_id: str,
    body: KbEvalGenerate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    coll = await db.get(KbCollection, collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    if not coll.chunk_count:
        raise APIError(409, "empty_collection", "ingest some documents first")

    from ..rag.eval.judge import Judge
    from ..rag.eval.synth import generate

    judge = Judge(model=body.model, embed_model=coll.embedding_model)
    cases = await generate(collection_id, n=body.n, judge=judge)
    return {"cases": cases}


@router.get("/eval-runs")
async def list_eval_runs(
    collection_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(KbEvalRun).order_by(KbEvalRun.created_at.desc()).limit(100)
    if collection_id:
        stmt = stmt.where(KbEvalRun.collection_id == collection_id)
    return [kb_eval_run_out(r) for r in (await db.exec(stmt)).all()]


@router.post("/eval-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_eval_run(
    body: KbEvalRunCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    coll = await db.get(KbCollection, body.collection_id)
    if coll is None:
        raise APIError(404, "not_found", "collection not found")
    if not body.cases:
        raise APIError(422, "no_cases", "at least one case is required")
    if body.config is not None:
        _validate_config(body.config)

    agent = await db.get(Agent, body.agent_id) if body.agent_id else None
    cfg = resolve_config(
        collection=coll.config,
        agent=(agent.rag_config if agent else {}) or {},
        request=body.config or {},
    )
    judge_model = body.judge_model or (agent.model if agent else None) or get_settings().summary_model

    run = KbEvalRun(
        collection_id=body.collection_id,
        name=body.name or "eval run",
        config_snapshot=cfg,
        agent_id=body.agent_id,
        judge_model=judge_model,
        case_count=len(body.cases),
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    for c in body.cases:
        db.add(KbEvalCase(run_id=run.id, question=c.question, ground_truth=c.ground_truth))
    await db.commit()

    from ..rag.eval import runner

    runner.schedule(run.id)
    return kb_eval_run_out(run)


@router.get("/eval-runs/{run_id}")
async def get_eval_run(
    run_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(KbEvalRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "eval run not found")
    cases = (
        await db.exec(select(KbEvalCase).where(KbEvalCase.run_id == run_id))
    ).all()
    return {"run": kb_eval_run_out(run), "cases": [kb_eval_case_out(c) for c in cases]}


@router.delete("/eval-runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eval_run(
    run_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(KbEvalRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "eval run not found")
    from sqlmodel import delete as sqldelete

    await db.exec(sqldelete(KbEvalCase).where(KbEvalCase.run_id == run_id))
    await db.delete(run)
    await db.commit()
