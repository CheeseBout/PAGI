"""Phase 16 — agent / trajectory evaluation (PLAN §16)."""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from app.db.models import Agent, AgentEvalCase, AgentEvalRun, ChatSession, Message
from app.db.session import SessionLocal
from app.eval.agent import runner
from app.eval.agent.metrics import trajectory_metrics
from app.providers import DoneEvent, TextDelta, ToolCallComplete


class EvalProvider:
    def __init__(self, model: str):
        self.model = model

    async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            yield TextDelta("done: the file says 42")
            yield DoneEvent(finish_reason="stop")
            return
        # first turn: call read_file twice (one redundant) then a forbidden tool
        yield ToolCallComplete(tool_call_id="t1", name="read_file", args={"path": "a.txt"})
        yield ToolCallComplete(tool_call_id="t2", name="read_file", args={"path": "a.txt"})
        yield DoneEvent(finish_reason="tool_calls")


class FakeJudge:
    def __init__(self, *a, **k):
        pass

    async def complete_json(self, system, user, **_kw):
        return {"resolved": "42" in user, "parameter_hallucination": False, "reason": "ok"}


@pytest.fixture
def eval_env(monkeypatch):
    monkeypatch.setattr("app.core.agent_runtime.get_provider", lambda n: EvalProvider(n))
    monkeypatch.setattr(
        "app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True
    )
    monkeypatch.setattr("app.eval.agent.runner.Judge", FakeJudge)

    # read_file tool returns a stub without hitting the sandbox
    async def _fake_read(ctx, args):
        return {"content": "42", "size_bytes": 2}

    from app.tools import TOOL_REGISTRY

    orig = TOOL_REGISTRY["read_file"].handler
    TOOL_REGISTRY["read_file"].handler = _fake_read
    yield
    TOOL_REGISTRY["read_file"].handler = orig


async def _agent() -> str:
    async with SessionLocal() as db:
        a = Agent(
            name="Ev", provider="openai", model="ev-model",
            tools_allowed=["read_file", "execute_code"],
            tool_policy={"read_file": "auto"},
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return a.id


@pytest.mark.asyncio
async def test_trajectory_metrics_counts_redundant_and_forbidden(auth_client, eval_env):
    from app.db.models import User

    async with SessionLocal() as db:
        uid = (await db.exec(select(User))).first().id
    aid = await _agent()
    async with SessionLocal() as db:
        s = ChatSession(user_id=uid, agent_id=aid)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        sid = s.id
        db.add(Message(session_id=sid, role="user", content="what does a.txt say?"))
        await db.commit()

    from app.core.agent_runtime import run_turn

    await run_turn(sid, wait_for_approval=True)

    async with SessionLocal() as db:
        m = await trajectory_metrics(
            db, sid, optimal_steps=2, forbidden_tools=["execute_code"]
        )
    assert m["redundant_tool_calls"] == 1  # read_file(a.txt) issued twice
    assert m["forbidden_tool_used"] is False  # execute_code never called
    assert m["steps_taken"] == 3  # 2 tool calls + 1 answer
    assert m["step_efficiency"] is not None


@pytest.mark.asyncio
async def test_full_agent_eval_run(auth_client, eval_env):
    aid = await _agent()
    r = await auth_client.post(
        "/api/agent-eval/runs",
        json={
            "name": "baseline",
            "agent_id": aid,
            "cases": [
                {
                    "prompt": "what does a.txt say?",
                    "expected_outcome": "42",
                    "optimal_steps": 2,
                    "forbidden_tools": ["execute_code"],
                }
            ],
        },
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]

    for _ in range(60):
        await asyncio.sleep(0.1)
        got = (await auth_client.get(f"/api/agent-eval/runs/{run_id}")).json()
        if got["run"]["status"] == "done":
            break
    assert got["run"]["status"] == "done", got
    assert got["run"]["case_count"] == 1
    case = got["cases"][0]
    assert case["resolved"] is True
    assert case["redundant_tool_calls"] == 1
    assert got["run"]["task_resolution_rate"] == 1.0

    # pattern override is validated
    bad = await auth_client.post(
        "/api/agent-eval/runs",
        json={"agent_id": aid, "pattern": "nonsense", "cases": [{"prompt": "x"}]},
    )
    assert bad.status_code == 422

    # list + delete
    lst = (await auth_client.get(f"/api/agent-eval/runs?agent_id={aid}")).json()
    assert any(x["id"] == run_id for x in lst)
    d = await auth_client.delete(f"/api/agent-eval/runs/{run_id}")
    assert d.status_code == 204


@pytest.mark.asyncio
async def test_ab_report_aggregates_per_arm(auth_client, eval_env):
    """The per-turn marker feeds /agent-eval/ab-report (SPEC §16.6)."""
    from app.core.agent_runtime import regenerate_last, run_turn
    from app.db.models import User

    async with SessionLocal() as db:
        uid = (await db.exec(select(User))).first().id
    async with SessionLocal() as db:
        a = Agent(
            name="ABr", provider="openai", model="ev-model", tools_allowed=[],
            orchestration={"pattern": "react"},
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        aid = a.id
        s = ChatSession(user_id=uid, agent_id=aid)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        sid = s.id
        db.add(Message(session_id=sid, role="user", content="hi"))
        await db.commit()

    await run_turn(sid, wait_for_approval=True)
    # a regenerate marks that turn as a dissatisfaction signal
    await regenerate_last(sid)

    rep = (await auth_client.get(f"/api/agent-eval/ab-report?agent_id={aid}")).json()
    arms = {a["pattern"]: a for a in rep["arms"]}
    assert "react" in arms
    react = arms["react"]
    assert react["turns"] >= 1
    assert react["regenerate_rate"] > 0  # the regenerate flagged a turn
    assert react["avg_llm_calls"] >= 1


@pytest.mark.asyncio
async def test_synth_cases_endpoint(auth_client, monkeypatch):
    aid = await _agent()

    async def _fake_synth(agent, *, n=8, judge=None):
        return [
            {"prompt": "read a.txt", "expected_outcome": "42", "optimal_steps": 2,
             "forbidden_tools": ["execute_code"]},
        ]

    monkeypatch.setattr("app.api.routes_agent_eval.synthesize", _fake_synth)
    r = await auth_client.post("/api/agent-eval/synth", json={"agent_id": aid, "n": 5})
    assert r.status_code == 202, r.text
    cases = r.json()["cases"]
    assert cases[0]["prompt"] == "read a.txt"
    assert cases[0]["forbidden_tools"] == ["execute_code"]

    bad = await auth_client.post("/api/agent-eval/synth", json={"agent_id": "nope"})
    assert bad.status_code == 404
