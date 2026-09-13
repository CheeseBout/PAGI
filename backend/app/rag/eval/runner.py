"""Run a golden set against a RagConfig and store per-case scores (SPEC §14.8).

For each case:  retrieve (with the run's config) -> generate a grounded answer ->
score the 4 metrics -> write a KbEvalCase.  Run averages + status land on the
KbEvalRun.  Runs in the background like ingest.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlmodel import select

from ...db.models import KbEvalCase, KbEvalRun
from ...db.session import SessionLocal
from ..pipeline import retrieve
from . import ragas
from .judge import Judge

log = structlog.get_logger("pagi.rag.eval.runner")

_ANSWER_SYS = (
    "Answer the question using ONLY the provided context. If the context does not "
    "contain the answer, say exactly: The documents do not contain this information."
)


def _mean(vals: list[float | None]) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 4) if nums else None


async def _answer(judge: Judge, question: str, contexts: list[str]) -> str:
    ctx = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(contexts)) or "(no context)"
    try:
        return (await judge.complete(_ANSWER_SYS, f"CONTEXT:\n{ctx}\n\nQUESTION: {question}")).strip()
    except Exception as exc:  # pragma: no cover - external
        log.warning("eval_answer_failed", error=str(exc))
        return ""


async def run(run_id: str, *, judge: Judge | None = None) -> None:
    async with SessionLocal() as db:
        run_row = await db.get(KbEvalRun, run_id)
        if run_row is None:
            return
        cases = list(
            (await db.exec(select(KbEvalCase).where(KbEvalCase.run_id == run_id))).all()
        )
        cfg = dict(run_row.config_snapshot or {})
        collection_id = run_row.collection_id
        judge = judge or Judge(model=run_row.judge_model)

    for case in cases:
        try:
            res = await retrieve(
                query=case.question,
                collection_ids=[collection_id],
                cfg=cfg,
                budget_tokens=cfg.get("context_max_tokens") or 4000,
                persist_log=False,
            )
            contexts = [c["text"] for c in res.chunks]
            answer = await _answer(judge, case.question, contexts)
            scores = await ragas.score_all(
                judge,
                question=case.question,
                answer=answer,
                contexts=contexts,
                ground_truth=case.ground_truth,
            )
        except Exception as exc:  # pragma: no cover - keep the run going
            log.warning("eval_case_failed", run_id=run_id, error=str(exc))
            contexts, answer = [], ""
            scores = {
                "faithfulness": None, "answer_relevancy": None,
                "context_precision": None, "context_recall": None,
                "rationale": {"error": str(exc)},
            }

        async with SessionLocal() as db:
            row = await db.get(KbEvalCase, case.id)
            if row is None:
                continue
            row.answer = answer
            row.contexts = contexts
            row.faithfulness = scores["faithfulness"]
            row.answer_relevancy = scores["answer_relevancy"]
            row.context_precision = scores["context_precision"]
            row.context_recall = scores["context_recall"]
            row.judge_rationale = scores["rationale"]
            db.add(row)
            await db.commit()

    async with SessionLocal() as db:
        run_row = await db.get(KbEvalRun, run_id)
        rows = list(
            (await db.exec(select(KbEvalCase).where(KbEvalCase.run_id == run_id))).all()
        )
        run_row.faithfulness = _mean([r.faithfulness for r in rows])
        run_row.answer_relevancy = _mean([r.answer_relevancy for r in rows])
        run_row.context_precision = _mean([r.context_precision for r in rows])
        run_row.context_recall = _mean([r.context_recall for r in rows])
        run_row.status = "done"
        db.add(run_row)
        await db.commit()
    log.info("eval_run_done", run_id=run_id, cases=len(cases))


def schedule(run_id: str) -> None:
    asyncio.create_task(run(run_id))
