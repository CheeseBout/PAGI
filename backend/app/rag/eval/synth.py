"""Golden-set generation — Evol-Instruct + a Critic filter (SPEC §14.8).

    seed    — sample chunks spread across the collection's documents
    base    — one grounded Q + ground_truth per seed
    evolve  — deepen (multi-hop over 2 chunks) / complicate (add a constraint)
    critic  — drop unclear / trivial / not-actually-answerable pairs
    negatives — a few plausible questions the docs do NOT answer

Returns ``[{question, ground_truth}]`` capped at ``n``.
"""

from __future__ import annotations

import random

import structlog
from sqlmodel import select

from ...db.models import KbChunk, KbDocument
from ...db.session import SessionLocal
from .judge import Judge

log = structlog.get_logger("pagi.rag.eval.synth")

_NEG_GT = "The documents do not contain this information."


async def _sample_chunks(collection_id: str, k: int) -> list[KbChunk]:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.exec(
                    select(KbChunk).where(
                        KbChunk.collection_id == collection_id, KbChunk.parent_id == None  # noqa: E711
                    )
                )
            ).all()
        )
    # keep it spread across documents: round-robin by document_id
    by_doc: dict[str, list[KbChunk]] = {}
    for c in rows:
        by_doc.setdefault(c.document_id, []).append(c)
    for lst in by_doc.values():
        random.shuffle(lst)
    out: list[KbChunk] = []
    docs = list(by_doc.values())
    random.shuffle(docs)
    i = 0
    while len(out) < k and any(docs):
        d = docs[i % len(docs)]
        if d:
            out.append(d.pop())
        i += 1
        docs = [d for d in docs if d]
        if not docs:
            break
    return out[:k]


async def generate(
    collection_id: str, *, n: int = 50, judge: Judge | None = None
) -> list[dict]:
    judge = judge or Judge()
    seeds = await _sample_chunks(collection_id, max(4, n))
    if not seeds:
        return []

    n_neg = max(1, n // 10)
    n_evolve = max(0, (n - n_neg) // 3)
    n_base = n - n_neg - n_evolve

    cases: list[dict] = []

    # ── base pairs ──────────────────────────────────────────────────
    for c in seeds[:n_base]:
        try:
            data = await judge.complete_json(
                "You write a factual QA pair answerable purely from a passage.",
                f"PASSAGE:\n{c.text[:1800]}\n\n"
                'Reply JSON: {"question":"...","answer":"..."} — the answer must be '
                "fully contained in the passage; no outside knowledge.",
            )
            q, a = data.get("question", "").strip(), data.get("answer", "").strip()
            if q and a:
                cases.append({"question": q, "ground_truth": a, "_kind": "base"})
        except Exception as exc:  # pragma: no cover
            log.warning("synth_base_failed", error=str(exc))

    # ── evolved (deepen / complicate) ─────────────────────────────
    pool = [c for c in seeds if c.text]
    for _ in range(n_evolve):
        if len(pool) < 2:
            break
        a_c, b_c = random.sample(pool, 2)
        style = random.choice(["deepen", "complicate"])
        try:
            data = await judge.complete_json(
                "You make a harder QA pair from one or two passages.",
                f"PASSAGE A:\n{a_c.text[:1200]}\n\nPASSAGE B:\n{b_c.text[:1200]}\n\n"
                + (
                    "Write a MULTI-HOP question needing both passages, plus its answer."
                    if style == "deepen"
                    else "Write a question about PASSAGE A with an added constraint or "
                    "qualifier, plus its answer."
                )
                + ' Reply JSON: {"question":"...","answer":"..."}',
            )
            q, a = data.get("question", "").strip(), data.get("answer", "").strip()
            if q and a:
                cases.append({"question": q, "ground_truth": a, "_kind": style})
        except Exception as exc:  # pragma: no cover
            log.warning("synth_evolve_failed", error=str(exc))

    # ── negatives (not in the docs) ──────────────────────────────
    if seeds:
        topic_sample = "\n".join(c.text[:300] for c in seeds[:5])
        try:
            data = await judge.complete_json(
                "You write questions that sound on-topic but are NOT answered by the given text.",
                f"TEXT SAMPLES:\n{topic_sample}\n\n"
                f'Write {n_neg} plausible questions a user might ask that this corpus '
                'does NOT answer. Reply JSON: {"questions":["...","..."]}',
            )
            for q in (data.get("questions") or [])[:n_neg]:
                if isinstance(q, str) and q.strip():
                    cases.append({"question": q.strip(), "ground_truth": _NEG_GT, "_kind": "negative"})
        except Exception as exc:  # pragma: no cover
            log.warning("synth_neg_failed", error=str(exc))

    # ── Critic filter ───────────────────────────────────────────
    kept: list[dict] = []
    for case in cases:
        if case["_kind"] == "negative":
            kept.append(case)
            continue
        try:
            verdict = await judge.complete_json(
                "You are a strict QA-set reviewer.",
                f'QUESTION: {case["question"]}\nANSWER: {case["ground_truth"]}\n\n'
                'Is this a clear, non-trivial question with a correct, self-contained '
                'answer? Reply JSON: {"keep":true|false,"reason":"..."}',
            )
            if verdict.get("keep"):
                kept.append(case)
        except Exception:  # pragma: no cover - keep on judge failure
            kept.append(case)

    random.shuffle(kept)
    return [{"question": c["question"], "ground_truth": c["ground_truth"]} for c in kept[:n]]
