"""Evaluator / Optimizer — generate -> evaluate -> optimize, loop (Phase 14d).

``max_rounds`` is a HARD ceiling: the stop condition must never depend only on a
score the model gives itself (SPEC §16.2).
"""

from __future__ import annotations

import structlog

from ._common import NodeLog, append_user, complete_json, last_assistant_text, run_react_once
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.evaluator_optimizer")

_EVAL_SYS = (
    "Score the candidate answer against the task from 0.0 to 1.0 and say what is "
    'wrong. Return JSON {"score": 0.0-1.0, "feedback": "..."}.'
)


class EvaluatorOptimizerStrategy:
    name = "evaluator_optimizer"

    async def drive(self, rc: RunContext) -> None:
        cfg = rc.cfg
        max_rounds = int(cfg.get("max_rounds") or 3)
        stop = float(cfg.get("stop_score") or 0.85)
        emit_mid = cfg.get("emit_intermediate", True)
        task = await _first_user_text(rc)

        for r in range(1, max_rounds + 1):
            async with NodeLog(rc, node="generate", step_no=(r - 1) * 3):
                answer = await run_react_once(rc)
            if not answer:
                answer = await last_assistant_text(rc)

            async with NodeLog(rc, node="evaluate", step_no=(r - 1) * 3 + 1) as nl:
                v = await complete_json(
                    rc,
                    system=_EVAL_SYS,
                    user=f"TASK:\n{task}\n\nCANDIDATE:\n{answer}",
                    model=cfg.get("evaluator_model"),
                    kind="evaluate",
                )
                score = _score(v)
                feedback = v.get("feedback", "") if isinstance(v, dict) else ""
                nl.payload = {"score": score, "feedback": feedback}
                nl.output_summary = f"score={score}"
            if emit_mid:
                await rc.emit_node({"type": "reflection", "attempt": r, "score": score, "reason": feedback[:300]})

            if score >= stop or r == max_rounds:
                return

            async with NodeLog(rc, node="execute", step_no=(r - 1) * 3 + 2):
                await append_user(
                    rc,
                    f"Your answer scored {score:.2f}. Improve it based on this feedback: "
                    f"{feedback}\nProduce the corrected final answer.",
                )


def _score(v) -> float:
    if isinstance(v, dict):
        try:
            return max(0.0, min(1.0, float(v.get("score"))))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


async def _first_user_text(rc: RunContext) -> str:
    from sqlmodel import select

    from ...db.models import Message

    row = (
        await rc.db.exec(
            select(Message)
            .where(Message.session_id == rc.session_id, Message.role == "user")
            .order_by(Message.created_at)
        )
    ).first()
    return (row.content or "") if row else ""


register(EvaluatorOptimizerStrategy())
