"""Phase 14 — design pattern strategies (SPEC §16)."""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.core.agent_runtime import run_turn
from app.core.patterns.base import _REGISTRY, StrategyUnavailable, get_strategy
from app.db.models import Agent, AgentRun, ChatSession, Message
from app.db.session import SessionLocal
from app.providers import DoneEvent, TextDelta, ToolCallComplete


class PatternProvider:
    """Scripted: model string decides behaviour; call index tracked per model."""

    calls: dict = {}

    def __init__(self, model: str):
        self.model = model

    async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
        PatternProvider.calls[model] = PatternProvider.calls.get(model, 0) + 1
        n = PatternProvider.calls[model]
        last = messages[-1] if messages else {}

        if model == "gen-model":
            # answer, low quality first time, better after a reflection message
            reflected = any(
                "Lesson learned" in (m.get("content") or "")
                for m in messages
                if m.get("role") == "user"
            )
            yield TextDelta("BETTER answer" if reflected else "weak answer")
            yield DoneEvent(finish_reason="stop")
        elif model == "eval-model":
            # evaluator: JSON wrapped in markdown fences, low then high
            reflected = "BETTER" in str(messages)
            score = 0.9 if reflected else 0.3
            yield TextDelta(f"```json\n{{\"score\": {score}, \"reason\": \"x\", "
                            f"\"missing_evidence\": [], \"spurious_claims\": []}}\n```")
            yield DoneEvent(finish_reason="stop")
        elif model == "reflect-model":
            yield TextDelta("failed because shallow; next time cite sources")
            yield DoneEvent(finish_reason="stop")
        elif model == "route-model":
            yield TextDelta('{"route": "code", "confidence": 0.9, "reason": "about code"}')
            yield DoneEvent(finish_reason="stop")
        elif model == "branch-model":
            yield TextDelta("branch handled it")
            yield DoneEvent(finish_reason="stop")
        elif model == "sup-plan":
            yield TextDelta(
                '{"subtasks": [{"agent": "Wok", "task": "a"}, '
                '{"agent": "Ghost", "task": "b"}]}'
            )
            yield DoneEvent(finish_reason="stop")
        elif model == "synth-model":
            yield TextDelta("SYNTH: combined answer")
            yield DoneEvent(finish_reason="stop")
        else:
            yield TextDelta("generic answer")
            yield DoneEvent(finish_reason="stop")


@pytest.fixture
def patterned(monkeypatch):
    PatternProvider.calls = {}
    monkeypatch.setattr(
        "app.core.agent_runtime.get_provider", lambda name: PatternProvider(name)
    )
    monkeypatch.setattr(
        "app.core.patterns._common.get_provider", lambda name: PatternProvider(name)
    )
    monkeypatch.setattr(
        "app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True
    )


async def _user_id() -> str:
    from app.db.models import User

    async with SessionLocal() as db:
        return (await db.exec(select(User))).first().id


async def _seed(agent: Agent) -> str:
    uid = await _user_id()
    async with SessionLocal() as db:
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        s = ChatSession(user_id=uid, agent_id=agent.id)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        db.add(Message(session_id=s.id, role="user", content="do the task"))
        await db.commit()
        return s.id


# ── unknown pattern never silently falls back (SPEC §16.2) ──────────────
def test_get_strategy_unavailable_raises():
    with pytest.raises(StrategyUnavailable):
        get_strategy("does_not_exist")


def test_all_seven_patterns_registered():
    assert set(_REGISTRY) == {
        "react", "reflexion", "plan_execute", "router",
        "supervisor", "debate", "evaluator_optimizer",
    }


# ── in-traffic A/B swaps the pattern by ab_split (SPEC §16.6) ──────────
@pytest.mark.asyncio
async def test_ab_split_runs_challenger(auth_client, patterned, monkeypatch):
    # force the RNG so the challenger is always chosen
    monkeypatch.setattr("app.core.agent_runtime.random.random", lambda: 0.0)
    sid = await _seed(
        Agent(
            name="AB", provider="openai", model="gen-model", tools_allowed=[],
            orchestration={
                "pattern": "react",
                "ab_pattern": "reflexion",
                "ab_split": 0.5,
                "max_attempts": 1,
                "evaluator_model": "eval-model",
            },
        )
    )
    await run_turn(sid, wait_for_approval=True)
    async with SessionLocal() as db:
        runs = (
            await db.exec(
                select(AgentRun).where(
                    AgentRun.session_id == sid, AgentRun.node != "turn"
                )
            )
        ).all()
    # reflexion writes generate/evaluate nodes; react writes none
    assert runs and all(r.pattern == "reflexion" for r in runs)


@pytest.mark.asyncio
async def test_ab_split_zero_keeps_primary(auth_client, patterned, monkeypatch):
    monkeypatch.setattr("app.core.agent_runtime.random.random", lambda: 0.0)
    sid = await _seed(
        Agent(
            name="AB0", provider="openai", model="gen-model", tools_allowed=[],
            orchestration={"pattern": "react", "ab_pattern": "reflexion", "ab_split": 0.0},
        )
    )
    await run_turn(sid, wait_for_approval=True)
    async with SessionLocal() as db:
        runs = (
            await db.exec(
                select(AgentRun).where(
                    AgentRun.session_id == sid, AgentRun.node != "turn"
                )
            )
        ).all()
        marker = (
            await db.exec(
                select(AgentRun).where(
                    AgentRun.session_id == sid, AgentRun.node == "turn"
                )
            )
        ).first()
    assert runs == []  # plain react, no pattern nodes
    assert marker is not None and marker.pattern == "react"  # A/B marker recorded the arm


# ── reflexion: retries, structured eval parsed from fenced JSON ─────────
@pytest.mark.asyncio
async def test_reflexion_retries_then_stops(auth_client, patterned):
    sid = await _seed(
        Agent(
            name="Rfx", provider="openai", model="gen-model", tools_allowed=[],
            orchestration={
                "pattern": "reflexion",
                "max_attempts": 3,
                "evaluator_model": "eval-model",
            },
        )
    )
    await run_turn(sid, wait_for_approval=True)

    async with SessionLocal() as db:
        msgs = (
            await db.exec(select(Message).where(Message.session_id == sid).order_by(Message.created_at))
        ).all()
        runs = (
            await db.exec(select(AgentRun).where(AgentRun.session_id == sid).order_by(AgentRun.step_no))
        ).all()

    # ended on the improved answer
    assert any("BETTER" in (m.content or "") for m in msgs)
    nodes = [r.node for r in runs]
    assert "generate" in nodes and "evaluate" in nodes and "reflect" in nodes
    # at least one reflect node => it retried, and it stopped once score was high
    evals = [r for r in runs if r.node == "evaluate"]
    assert evals[-1].payload.get("score") == 0.9


# ── reflexion honours max_attempts as a hard cap ──────────────────────
@pytest.mark.asyncio
async def test_reflexion_hard_cap(auth_client, monkeypatch):
    PatternProvider.calls = {}

    class AlwaysBad(PatternProvider):
        async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
            if model == "eval-model":
                yield TextDelta('{"score": 0.1, "reason": "no"}')
            elif model == "reflect-model":
                yield TextDelta("try harder")
            else:
                yield TextDelta("still bad")
            yield DoneEvent(finish_reason="stop")

    monkeypatch.setattr("app.core.agent_runtime.get_provider", lambda n: AlwaysBad(n))
    monkeypatch.setattr("app.core.patterns._common.get_provider", lambda n: AlwaysBad(n))
    monkeypatch.setattr("app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True)

    sid = await _seed(
        Agent(
            name="Cap", provider="openai", model="gen", tools_allowed=[],
            orchestration={"pattern": "reflexion", "max_attempts": 2,
                           "evaluator_model": "eval-model"},
        )
    )
    await run_turn(sid, wait_for_approval=True)
    async with SessionLocal() as db:
        gens = (
            await db.exec(
                select(AgentRun).where(AgentRun.session_id == sid, AgentRun.node == "generate")
            )
        ).all()
    assert len(gens) == 2  # exactly max_attempts, no more


# ── router picks a branch and records the decision ───────────────────
@pytest.mark.asyncio
async def test_router_takes_branch(auth_client, patterned):
    sid = await _seed(
        Agent(
            name="Rt", provider="openai", model="branch-model", tools_allowed=[],
            orchestration={
                "pattern": "router",
                "router_model": "route-model",
                "routes": [
                    {"name": "code", "description": "coding questions"},
                    {"name": "prose", "description": "writing"},
                ],
            },
        )
    )
    await run_turn(sid, wait_for_approval=True)
    async with SessionLocal() as db:
        route = (
            await db.exec(
                select(AgentRun).where(AgentRun.session_id == sid, AgentRun.node == "route")
            )
        ).first()
        msgs = (await db.exec(select(Message).where(Message.session_id == sid))).all()
    assert route.payload.get("route") == "code"
    assert any("branch handled" in (m.content or "") for m in msgs)


# ── supervisor: one subtask names a missing agent, synthesis still runs ──
@pytest.mark.asyncio
async def test_supervisor_survives_worker_error(auth_client, patterned):
    uid = await _user_id()
    async with SessionLocal() as db:
        w_ok = Agent(name="Wok", provider="openai", model="gen-model",
                     is_delegatable=True, delegate_description="ok worker", tools_allowed=[])
        db.add(w_ok)
        await db.commit()
        await db.refresh(w_ok)
        boss = Agent(
            name="Sup", provider="openai", model="gen-model", tools_allowed=["delegate_task"],
            orchestration={
                "pattern": "supervisor",
                "supervisor_model": "sup-plan",
                "synthesis_model": "synth-model",
                "worker_agent_ids": [w_ok.id],
                "parallel": True,
            },
        )
        db.add(boss)
        await db.commit()
        await db.refresh(boss)
        s = ChatSession(user_id=uid, agent_id=boss.id)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        sid = s.id
        db.add(Message(session_id=sid, role="user", content="split this"))
        await db.commit()

    await run_turn(sid, wait_for_approval=True)

    async with SessionLocal() as db:
        msgs = (await db.exec(select(Message).where(Message.session_id == sid))).all()
        runs = (await db.exec(select(AgentRun).where(AgentRun.session_id == sid))).all()
    assert any("SYNTH" in (m.content or "") for m in msgs)
    assert any(r.node == "synthesize" for r in runs)
    # the planner proposed a subtask for a non-existent agent "Ghost" — it must
    # have been dropped, leaving exactly one delegate node
    assert sum(1 for r in runs if r.node == "delegate") == 1
