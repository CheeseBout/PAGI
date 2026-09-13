"""Ingest-time enrichment (SPEC §14.2 group 1, §14.3).

    enrich_summary          — 1 LLM call/doc → doc summary, into every chunk's meta
    enrich_metadata_extract — 1 LLM call/doc → {author,date,doc_type,entities},
                              merged into the document's meta (so metadata_filter
                              can use it) and every chunk's meta
    enrich_contextual       — 1 LLM call/chunk → a situating sentence PREPENDED to
                              ``text_embedded`` (not ``text``) — Anthropic's
                              contextual-retrieval trick
    enrich_hyqa             — 1 LLM call/chunk → N hypothetical questions, also
                              prepended to ``text_embedded`` so question-style
                              queries match

Only ``text_embedded`` and ``meta`` change; ``text`` (what the model sees) is
untouched. All 🔁 — applied on ingest / re-ingest only.

Simplification vs. the paper: HyQA questions ride on the chunk's own embedding
rather than getting their own vector rows, so ``chunk_count`` stays 1-per-chunk.
"""

from __future__ import annotations

import asyncio
import json
import re

import structlog

from ..config import get_settings

log = structlog.get_logger("pagi.rag.enrich")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def wants_enrichment(cfg: dict) -> bool:
    return any(
        cfg.get(k)
        for k in ("enrich_contextual", "enrich_summary", "enrich_hyqa", "enrich_metadata_extract")
    )


async def _llm(model: str | None, provider: str | None, system: str, user: str, *, max_tokens: int) -> str:
    from ..providers import DoneEvent, TextDelta, UsageEvent, get_provider

    if not model:
        return ""
    try:
        prov = get_provider(provider or "openai")
    except Exception:
        prov = get_provider("openai")
    parts: list[str] = []
    try:
        async for ev in prov.stream_chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[], model=model, temperature=0.0, max_tokens=max_tokens,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, (DoneEvent, UsageEvent)):
                continue
    except Exception as exc:  # pragma: no cover - external
        log.warning("enrich_llm_failed", error=str(exc))
        return ""
    return "".join(parts).strip()


async def enrich(
    chunks: list,
    *,
    full_text: str,
    cfg: dict,
    model: str | None,
    provider: str | None,
) -> dict:
    """Mutates ``chunks`` in place (``text_embedded`` / ``meta``). Returns the
    document-level meta additions to merge into ``kb_documents.meta``."""
    model = cfg.get("enrich_contextual_model") or model or get_settings().summary_model
    if not model:
        log.info("enrich_skipped", reason="no model available")
        return {}

    doc_meta: dict = {}
    overview = full_text[:2500]

    # ── document-level (1 call each) ──────────────────────────────
    summary = ""
    if cfg.get("enrich_summary"):
        summary = await _llm(
            model, provider,
            "Summarise the document in 2-3 sentences. Plain text only.",
            overview, max_tokens=180,
        )

    if cfg.get("enrich_metadata_extract"):
        raw = await _llm(
            model, provider,
            "Extract document metadata. Reply JSON only: "
            '{"author":"","date":"","doc_type":"","entities":[]} — use "" / [] when unknown.',
            overview, max_tokens=200,
        )
        m = _JSON_RE.search(raw)
        if m:
            try:
                parsed = json.loads(m.group(0))
                for k in ("author", "date", "doc_type"):
                    if parsed.get(k):
                        doc_meta[k] = parsed[k]
                if isinstance(parsed.get("entities"), list) and parsed["entities"]:
                    doc_meta["entities"] = [str(e) for e in parsed["entities"]][:20]
            except ValueError:
                log.warning("enrich_metadata_parse_failed")

    for c in chunks:
        if summary:
            c.meta = {**c.meta, "doc_summary": summary}
        if doc_meta:
            c.meta = {**c.meta, **doc_meta}

    # ── per-chunk (concurrent, bounded) ──────────────────────────
    need_ctx = bool(cfg.get("enrich_contextual"))
    need_hyqa = bool(cfg.get("enrich_hyqa"))
    if not (need_ctx or need_hyqa):
        return doc_meta

    hyqa_n = int(cfg.get("enrich_hyqa_n") or 3)
    sem = asyncio.Semaphore(max(1, get_settings().rag_enrich_concurrency))

    async def _one(c) -> None:
        prefix_parts: list[str] = []
        async with sem:
            if need_ctx:
                ctx = await _llm(
                    model, provider,
                    "Give a short sentence situating the chunk within the document "
                    "(what section / topic it belongs to). Plain text, one sentence.",
                    f"DOCUMENT OVERVIEW:\n{overview}\n\nCHUNK:\n{c.text[:1500]}",
                    max_tokens=80,
                )
                if ctx:
                    prefix_parts.append(ctx)
            if need_hyqa:
                q = await _llm(
                    model, provider,
                    f"Write {hyqa_n} short questions this chunk answers, one per line, no numbering.",
                    c.text[:1500], max_tokens=160,
                )
                lines = [ln.strip().lstrip("-*0123456789. )").strip() for ln in q.splitlines()]
                prefix_parts.extend(ln for ln in lines if ln)
        if prefix_parts:
            c.text_embedded = "\n".join(prefix_parts) + "\n\n" + c.text

    await asyncio.gather(*(_one(c) for c in chunks))
    return doc_meta
