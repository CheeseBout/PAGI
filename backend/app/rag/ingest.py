"""Ingest orchestrator (SPEC §14.3).

    load -> hash -> injection scan -> chunk -> (enrich: 12d) -> embed -> upsert

Rules:
  - runs in the background; the route returns 202 immediately
  - a document's chunks are replaced in a single transaction (no window where the
    collection is empty or mixes versions)
  - same (collection, source_uri, content_hash) => no-op (idempotent)
  - failure => status="error" with the message, and no partial chunks
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

import structlog
from sqlmodel import delete as sqldelete
from sqlmodel import select

from ..config import get_settings
from ..core.security import scan_for_injection
from ..db.models import KbChunk, KbCollection, KbDocument
from ..db.session import SessionLocal
from ..rag import docstore
from . import enrich as enrich_mod
from .bm25 import get_sparse_store
from .chunkers import build_chunks
from .config import resolve_config
from .embeddings import EmbeddingError, embed_texts
from .loaders import LoaderError, LoadedDoc, load_bytes, load_url
from .store import get_store

log = structlog.get_logger("pagi.rag.ingest")

_ingest_sem: asyncio.Semaphore | None = None
_ingest_sem_loop: object | None = None


def _sem() -> asyncio.Semaphore:
    """Concurrency gate, rebound if the running loop changed (matters only for
    tests — the app runs one loop for its lifetime)."""
    global _ingest_sem, _ingest_sem_loop
    loop = asyncio.get_running_loop()
    if _ingest_sem is None or _ingest_sem_loop is not loop:
        _ingest_sem = asyncio.Semaphore(max(1, get_settings().rag_ingest_concurrency))
        _ingest_sem_loop = loop
    return _ingest_sem


_doc_locks: dict[str, asyncio.Lock] = {}
_doc_locks_loop: object | None = None


def _doc_lock(doc_id: str) -> asyncio.Lock:
    """Serialize ingests of the *same* document (double-click re-ingest, a
    leaked task) so they can't race on the chunk rewrite."""
    global _doc_locks, _doc_locks_loop
    loop = asyncio.get_running_loop()
    if _doc_locks_loop is not loop:
        _doc_locks = {}
        _doc_locks_loop = loop
    return _doc_locks.setdefault(doc_id, asyncio.Lock())


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _source_text(doc: KbDocument) -> LoadedDoc:
    if doc.source_type == "url":
        return await load_url(doc.source_uri)
    blob = docstore.load(doc.id)
    if blob is None:
        raise LoaderError("source file is missing (was it deleted?)")
    data, ext = blob
    ct = doc.meta.get("content_type", "")
    return load_bytes(filename=doc.source_uri or f"doc.{ext}", content_type=ct, data=data)


async def run(doc_id: str) -> None:
    """Full ingest for one document. Safe to call from ``asyncio.create_task``
    and safe to call twice concurrently for the same doc (the second waits)."""
    async with _doc_lock(doc_id):
        async with _sem():
            await _run_locked(doc_id)


async def _run_locked(doc_id: str) -> None:
    settings = get_settings()
    async with SessionLocal() as db:
        doc = await db.get(KbDocument, doc_id)
        if doc is None:
            return
        coll = await db.get(KbCollection, doc.collection_id)
        if coll is None:
            return
        cfg = resolve_config(collection=coll.config)

        async def _fail(msg: str) -> None:
            doc.status = "error"
            doc.error = msg[:2000]
            doc.updated_at = _now()
            db.add(doc)
            await db.commit()
            log.warning("ingest_failed", doc_id=doc_id, error=msg)

        try:
            doc.status = "parsing"
            db.add(doc)
            await db.commit()
            loaded = await _source_text(doc)
        except Exception as exc:  # noqa: BLE001 - any parse failure -> error status
            await _fail(f"parse: {exc}")
            return

        content_hash = hashlib.sha256(loaded.text.encode("utf-8")).hexdigest()
        # idempotent: unchanged content and already-ready => nothing to do
        existing_ready = (
            await db.exec(
                select(KbChunk.id).where(KbChunk.document_id == doc.id).limit(1)
            )
        ).first()
        if doc.content_hash == content_hash and existing_ready and doc.status not in ("error",):
            doc.status = "ready"
            doc.updated_at = _now()
            db.add(doc)
            await db.commit()
            log.info("ingest_unchanged", doc_id=doc_id)
            return

        if doc.content_hash and doc.content_hash != content_hash:
            doc.version += 1

        flagged = bool(scan_for_injection(loaded.text))

        doc.status = "chunking"
        db.add(doc)
        await db.commit()
        cset = await build_chunks(loaded.text, cfg, embed_model=coll.embedding_model)
        chunks = cset.chunks
        if not chunks:
            await _fail("no chunks produced")
            return

        cap = settings.rag_max_chunks_per_collection
        other = (
            await db.exec(
                select(KbChunk.id).where(
                    KbChunk.collection_id == coll.id, KbChunk.document_id != doc.id
                )
            )
        ).all()
        if len(other) + len(chunks) + len(cset.parents) > cap:
            await _fail(
                f"collection chunk cap ({cap}) exceeded — raise "
                f"RAG_MAX_CHUNKS_PER_COLLECTION or use pgvector"
            )
            return

        # ── enrichment (12d, 🔁) — mutates text_embedded / meta only ──
        enrich_doc_meta: dict = {}
        if enrich_mod.wants_enrichment(cfg):
            doc.status = "enriching"
            db.add(doc)
            await db.commit()
            try:
                enrich_doc_meta = await enrich_mod.enrich(
                    chunks, full_text=loaded.text, cfg=cfg,
                    model=cfg.get("enrich_contextual_model"), provider=None,
                )
            except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
                log.warning("enrich_failed", doc_id=doc_id, error=str(exc))

        doc.status = "embedding"
        db.add(doc)
        await db.commit()
        try:
            vectors = await embed_texts(
                coll.embedding_model,
                [c.embed_text() for c in chunks],
                batch_size=cfg.get("embedding_batch_size"),
            )
        except EmbeddingError as exc:
            await _fail(f"embed: {exc}")
            return

        dim = len(vectors[0]) if vectors else 0
        if coll.embedding_dim and dim and coll.embedding_dim != dim:
            await _fail(
                f"embedding dim {dim} != collection dim {coll.embedding_dim}"
            )
            return

        # ── replace this document's chunks atomically ──────────────────
        await db.exec(sqldelete(KbChunk).where(KbChunk.document_id == doc.id))
        # parent_child: insert parents first (no embedding), map ordinal -> row id
        parent_row_id: dict[int, str] = {}
        for p in cset.parents:
            prow = KbChunk(
                document_id=doc.id,
                collection_id=coll.id,
                ordinal=p.ordinal,
                text=p.text,
                text_embedded="",
                embedding=[],
                token_count=p.token_count,
                meta={**p.meta, **loaded.meta, "is_parent": True},
            )
            db.add(prow)
            await db.flush()
            parent_row_id[p.ordinal] = prow.id
        for c, vec in zip(chunks, vectors):
            db.add(
                KbChunk(
                    document_id=doc.id,
                    collection_id=coll.id,
                    parent_id=(
                        parent_row_id.get(c.parent_ordinal)
                        if c.parent_ordinal is not None
                        else None
                    ),
                    ordinal=c.ordinal,
                    text=c.text,
                    text_embedded=c.embed_text(),
                    embedding=vec,
                    token_count=c.token_count,
                    meta={**c.meta, **loaded.meta},
                )
            )
        doc.content_hash = content_hash
        doc.token_count = sum(c.token_count for c in chunks)
        doc.injection_flagged = flagged
        if enrich_doc_meta:
            doc.meta = {**(doc.meta or {}), **enrich_doc_meta}
        doc.status = "ready"
        doc.error = None
        doc.updated_at = _now()
        if loaded.title and not doc.title:
            doc.title = loaded.title[:512]
        db.add(doc)
        await db.commit()

    await recount_collection(doc.collection_id, embedding_dim=len(vectors[0]) if vectors else None)
    invalidate_indexes(doc.collection_id)
    log.info("ingest_ok", doc_id=doc_id, chunks=len(chunks), parents=len(cset.parents))


def invalidate_indexes(collection_id: str) -> None:
    """Drop the cached dense + sparse indexes for a collection."""
    get_store().invalidate(collection_id)
    get_sparse_store().invalidate(collection_id)


async def recount_collection(collection_id: str, *, embedding_dim: int | None = None) -> None:
    async with SessionLocal() as db:
        coll = await db.get(KbCollection, collection_id)
        if coll is None:
            return
        n_chunks = len(
            (await db.exec(select(KbChunk.id).where(KbChunk.collection_id == collection_id))).all()
        )
        n_docs = len(
            (
                await db.exec(
                    select(KbDocument.id).where(
                        KbDocument.collection_id == collection_id,
                        KbDocument.status == "ready",
                    )
                )
            ).all()
        )
        coll.chunk_count = n_chunks
        coll.doc_count = n_docs
        if embedding_dim and not coll.embedding_dim:
            coll.embedding_dim = embedding_dim
        coll.status = "ready"
        coll.updated_at = _now()
        db.add(coll)
        await db.commit()


async def sweep_interrupted() -> int:
    """On boot, flag documents left mid-ingest by a crash (SPEC §14.3)."""
    async with SessionLocal() as db:
        rows = list(
            (
                await db.exec(
                    select(KbDocument).where(
                        KbDocument.status.in_(
                        ["pending", "parsing", "chunking", "enriching", "embedding"]
                    )
                    )
                )
            ).all()
        )
        for d in rows:
            d.status = "error"
            d.error = "ingest interrupted by a restart — re-ingest this document"
            d.updated_at = _now()
            db.add(d)
        if rows:
            await db.commit()
    return len(rows)
