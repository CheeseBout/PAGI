"""The four RAGAS-style metrics (SPEC §14.8), LLM-as-judge.

    faithfulness       — answer claims supported by the retrieved context
    answer_relevancy   — answer actually addresses the question
    context_precision  — relevant chunks ranked high (Average Precision)
    context_recall     — context covers the ground-truth answer (needs ground_truth)

Each returns ``MetricResult(score | None, rationale)``. ``None`` means "not
computable" (e.g. recall without a ground truth) — never a guessed 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import structlog

from .judge import Judge

log = structlog.get_logger("pagi.rag.eval.ragas")


@dataclass
class MetricResult:
    score: float | None
    rationale: dict


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def faithfulness(judge: Judge, *, answer: str, contexts: list[str]) -> MetricResult:
    answer = (answer or "").strip()
    if not answer:
        return MetricResult(None, {"skipped": "empty answer"})
    ctx = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(contexts)) or "(no context)"
    try:
        data = await judge.complete_json(
            "You verify whether an answer is grounded in the given context.",
            "Break the ANSWER into atomic factual statements, then for each say if "
            "it is supported by the CONTEXT.\n\n"
            f"CONTEXT:\n{ctx}\n\nANSWER:\n{answer}\n\n"
            'Reply JSON: {"statements":[{"text":"...","supported":true|false}]}',
        )
        stmts = data.get("statements") or []
    except Exception as exc:  # pragma: no cover - external
        return MetricResult(None, {"error": str(exc)})
    if not stmts:
        return MetricResult(None, {"note": "no statements extracted"})
    supported = sum(1 for s in stmts if s.get("supported"))
    return MetricResult(
        round(supported / len(stmts), 4),
        {"supported": supported, "total": len(stmts), "statements": stmts[:12]},
    )


async def answer_relevancy(judge: Judge, *, question: str, answer: str, n: int = 3) -> MetricResult:
    answer = (answer or "").strip()
    if not answer:
        return MetricResult(None, {"skipped": "empty answer"})
    try:
        data = await judge.complete_json(
            "You reverse-engineer the question an answer responds to.",
            f"Given this ANSWER, write {n} distinct questions it would be a direct "
            f"answer to.\n\nANSWER:\n{answer}\n\n"
            'Reply JSON: {"questions":["...","..."]}',
        )
        gen = [q for q in (data.get("questions") or []) if isinstance(q, str)][:n]
    except Exception as exc:  # pragma: no cover
        return MetricResult(None, {"error": str(exc)})
    if not gen:
        return MetricResult(None, {"note": "no questions generated"})
    try:
        vecs = await judge.embed([question] + gen)
    except Exception as exc:  # pragma: no cover
        return MetricResult(None, {"error": f"embed: {exc}"})
    qv, gvs = vecs[0], vecs[1:]
    sims = [max(0.0, _cos(qv, g)) for g in gvs]
    return MetricResult(round(sum(sims) / len(sims), 4), {"generated": gen, "sims": [round(s, 3) for s in sims]})


async def context_precision(
    judge: Judge, *, question: str, contexts: list[str], ground_truth: str | None
) -> MetricResult:
    if not contexts:
        return MetricResult(None, {"skipped": "no contexts"})
    target = ground_truth or question
    try:
        data = await judge.complete_json(
            "You judge whether each context chunk helps answer the question.",
            f"QUESTION: {question}\nREFERENCE ANSWER: {target}\n\n"
            + "\n\n".join(f"[{i}] {c[:1500]}" for i, c in enumerate(contexts))
            + '\n\nFor each chunk index, is it useful for producing the reference '
            'answer? Reply JSON: {"relevant":[true|false, ...]} in index order.',
        )
        flags = [bool(x) for x in (data.get("relevant") or [])][: len(contexts)]
    except Exception as exc:  # pragma: no cover
        return MetricResult(None, {"error": str(exc)})
    if not flags:
        return MetricResult(None, {"note": "no judgement"})
    # Average Precision over the ranked list
    hits = 0
    ap = 0.0
    for k, rel in enumerate(flags, start=1):
        if rel:
            hits += 1
            ap += hits / k
    score = (ap / hits) if hits else 0.0
    return MetricResult(round(score, 4), {"relevant": flags})


async def context_recall(
    judge: Judge, *, contexts: list[str], ground_truth: str | None
) -> MetricResult:
    if not ground_truth or not ground_truth.strip():
        return MetricResult(None, {"skipped": "no ground_truth"})
    if not contexts:
        return MetricResult(0.0, {"note": "no contexts retrieved"})
    ctx = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(contexts))
    try:
        data = await judge.complete_json(
            "You check whether a reference answer is covered by the context.",
            "Break the REFERENCE ANSWER into atomic statements, then for each say "
            "if it can be attributed to the CONTEXT.\n\n"
            f"CONTEXT:\n{ctx}\n\nREFERENCE ANSWER:\n{ground_truth}\n\n"
            'Reply JSON: {"statements":[{"text":"...","attributed":true|false}]}',
        )
        stmts = data.get("statements") or []
    except Exception as exc:  # pragma: no cover
        return MetricResult(None, {"error": str(exc)})
    if not stmts:
        return MetricResult(None, {"note": "no statements"})
    attributed = sum(1 for s in stmts if s.get("attributed"))
    return MetricResult(
        round(attributed / len(stmts), 4),
        {"attributed": attributed, "total": len(stmts), "statements": stmts[:12]},
    )


async def score_all(
    judge: Judge, *, question: str, answer: str, contexts: list[str], ground_truth: str | None
) -> dict:
    f = await faithfulness(judge, answer=answer, contexts=contexts)
    ar = await answer_relevancy(judge, question=question, answer=answer)
    cp = await context_precision(
        judge, question=question, contexts=contexts, ground_truth=ground_truth
    )
    cr = await context_recall(judge, contexts=contexts, ground_truth=ground_truth)
    return {
        "faithfulness": f.score,
        "answer_relevancy": ar.score,
        "context_precision": cp.score,
        "context_recall": cr.score,
        "rationale": {
            "faithfulness": f.rationale,
            "answer_relevancy": ar.rationale,
            "context_precision": cp.rationale,
            "context_recall": cr.rationale,
        },
    }
