"""Debate — N agents answer independently, critique, a judge decides (Phase 14d).

Society of Mind: distinct models per role reduce correlated errors. Same model
for every role keeps the cost multiplied by N without cutting collective-delusion
risk — hence ``require_distinct_models`` warns.
"""

from __future__ import annotations

import asyncio

import structlog

from ._common import NodeLog, simple_llm
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.debate")

_JUDGE_SYS = (
    "You are the judge of a debate. You are given the question and each debater's "
    "final position. First give a brief rationale, THEN on the last line output "
    '`WINNER: <agent name>`. Base the decision on correctness and evidence, not '
    "length or confidence."
)


class DebateStrategy:
    name = "debate"

    async def drive(self, rc: RunContext) -> None:
        from ...db.models import Agent, Message
        from ..delegation import run_delegated

        cfg = rc.cfg
        rounds = int(cfg.get("rounds") or 1)
        emit_mid = cfg.get("emit_intermediate", True)
        task = await _first_user_text(rc)

        ids = cfg.get("debater_agent_ids") or []
        debaters = [d for d in [await rc.db.get(Agent, i) for i in ids] if d]
        if len(debaters) < 2:
            from ._common import run_react_once

            await run_react_once(rc)
            return

        if cfg.get("require_distinct_models", True) and len({d.model for d in debaters}) < len(debaters):
            log.warning("debate_shared_models", session_id=rc.session_id)

        positions: dict[str, str] = {}

        async def _ask(d: Agent, idx: int, prompt: str) -> None:
            async with NodeLog(rc, node="generate", step_no=idx) as nl:
                res = await run_delegated(
                    parent_session_id=rc.session_id,
                    tool_call_id=f"deb:{rc.session_id}:{d.id}:{idx}",
                    worker_name=d.name,
                    task=prompt,
                    wait_for_approval=rc.wait_for_approval,
                    mode=rc.mode,
                    unattended_allowed_tools=rc.unattended_allowed_tools,
                )
                positions[d.name] = res.get("answer") or res.get("message") or ""
                nl.output_summary = positions[d.name][:200]

        # round 0: independent answers
        await asyncio.gather(*[_ask(d, i, task) for i, d in enumerate(debaters)])

        # critique rounds
        step = len(debaters)
        for r in range(1, rounds + 1):
            others = lambda me: "\n\n".join(  # noqa: E731
                f"[{n}] {p}" for n, p in positions.items() if n != me
            )
            await asyncio.gather(
                *[
                    _ask(
                        d,
                        step + i,
                        f"{task}\n\nOther agents said:\n{others(d.name)}\n\n"
                        "Critique their answers and give your revised final position.",
                    )
                    for i, d in enumerate(debaters)
                ]
            )
            step += len(debaters)

        async with NodeLog(rc, node="judge", step_no=step) as nl:
            board = "\n\n".join(f"[{n}]\n{p}" for n, p in positions.items())
            verdict = await simple_llm(
                rc,
                system=_JUDGE_SYS,
                user=f"QUESTION:\n{task}\n\nPOSITIONS:\n{board}",
                model=cfg.get("judge_model"),
                kind="judge",
                max_tokens=600,
            )
            winner = _winner_of(verdict, list(positions))
            nl.payload = {"winner": winner}
            nl.output_summary = f"winner={winner}"
        if emit_mid:
            await rc.emit_node({"type": "debate_vote", "winner": winner, "rationale": verdict[:400]})

        final = positions.get(winner) or verdict
        rc.db.add(Message(session_id=rc.session_id, role="assistant", content=final))
        await rc.db.commit()
        await rc.emit({"type": "message_done", "message_id": None, "tokens_in": 0, "tokens_out": 0})


def _winner_of(verdict: str, names: list[str]) -> str:
    tail = verdict.rsplit("WINNER:", 1)
    if len(tail) == 2:
        claim = tail[1].strip().strip(".").strip()
        for n in names:
            if n.lower() in claim.lower():
                return n
    for n in names:
        if n.lower() in verdict.lower():
            return n
    return names[0]


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


register(DebateStrategy())
