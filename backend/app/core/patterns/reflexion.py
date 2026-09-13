"""Reflexion — Generate -> Evaluate -> Reflect -> Retry (Phase 14b, SPEC §16.2).

Stops on ``score >= success_score`` or ``attempt >= max_attempts``. The
evaluator MUST return structured output; the reflection carried into the next
attempt is a SHORT lesson, never the whole trace (context-bloat guard).
"""

from __future__ import annotations

import json

import structlog

from ._common import NodeLog, append_user, complete_json, last_assistant_text, run_react_once
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.reflexion")

_EVAL_SYS = (
    "You are a strict evaluator. Given a task and a candidate answer, judge how "
    "well the answer solves the task. Return JSON: "
    '{"score": 0.0-1.0, "reason": "...", "missing_evidence": [...], '
    '"spurious_claims": [...]}'
)


class ReflexionStrategy:
    name = "reflexion"

    async def drive(self, rc: RunContext) -> None:
        cfg = rc.cfg
        max_attempts = int(cfg.get("max_attempts") or 3)
        success = float(cfg.get("success_score") or 0.8)
        refl_cap = int(cfg.get("reflection_max_chars") or 500)
        emit_mid = cfg.get("emit_intermediate", True)

        task = await _first_user_text(rc)

        for attempt in range(1, max_attempts + 1):
            async with NodeLog(rc, node="generate", step_no=(attempt - 1) * 3, attempt=attempt):
                answer = await run_react_once(rc)
            if not answer:
                answer = await last_assistant_text(rc)

            async with NodeLog(
                rc, node="evaluate", step_no=(attempt - 1) * 3 + 1, attempt=attempt
            ) as nl:
                verdict = await complete_json(
                    rc,
                    system=_EVAL_SYS,
                    user=f"TASK:\n{task}\n\nCANDIDATE ANSWER:\n{answer}",
                    model=cfg.get("evaluator_model"),
                    kind="evaluate",
                )
                score = _score_of(verdict)
                nl.payload = {"score": score, "verdict": verdict}
                nl.output_summary = f"score={score}"

            if emit_mid:
                await rc.emit_node(
                    {
                        "type": "reflection",
                        "attempt": attempt,
                        "score": score,
                        "reason": (verdict or {}).get("reason", "")[:400]
                        if isinstance(verdict, dict) else "",
                    }
                )

            if score >= success or attempt == max_attempts:
                return

            async with NodeLog(
                rc, node="reflect", step_no=(attempt - 1) * 3 + 2, attempt=attempt
            ) as nl:
                lesson = await _reflect(rc, task, answer, verdict, refl_cap)
                nl.payload = {"lesson": lesson}
                nl.output_summary = lesson[:200]
            if emit_mid:
                await rc.emit_node({"type": "reflection", "attempt": attempt, "lesson": lesson})
            await append_user(
                rc,
                "Your previous answer was judged insufficient. "
                f"Lesson learned: {lesson}\n"
                "Try again and produce a corrected, complete final answer.",
            )


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


def _score_of(verdict) -> float:
    if isinstance(verdict, dict):
        try:
            return max(0.0, min(1.0, float(verdict.get("score"))))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


async def _reflect(rc: RunContext, task: str, answer: str, verdict, cap: int) -> str:
    reason = ""
    if isinstance(verdict, dict):
        reason = json.dumps(
            {
                "reason": verdict.get("reason"),
                "missing_evidence": verdict.get("missing_evidence"),
                "spurious_claims": verdict.get("spurious_claims"),
            }
        )
    lesson = await simple_reflect(rc, task, answer, reason)
    return lesson[:cap]


async def simple_reflect(rc: RunContext, task: str, answer: str, reason: str) -> str:
    from ._common import simple_llm

    return await simple_llm(
        rc,
        system=(
            "In one or two sentences, state the single most important lesson for the "
            "next attempt, in the form 'failed because X; next time do Y'. Be terse."
        ),
        user=f"TASK: {task}\nANSWER: {answer}\nEVALUATION: {reason}",
        model=rc.cfg.get("evaluator_model"),
        kind="reflect",
        max_tokens=160,
    )


register(ReflexionStrategy())
