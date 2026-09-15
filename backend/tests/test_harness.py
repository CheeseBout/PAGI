"""Phase 17 — self-improving harness (PLAN §17)."""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from app.db.models import (
    Agent,
    AgentConfigVersion,
    AgentEvalCase,
    AgentEvalRun,
    User,
    WeaknessReport,
)
from app.db.session import SessionLocal
from app.harness import mine, propose, rollback, validate
from app.providers import DoneEvent, TextDelta


# ── fixtures / helpers ───────────────────────────────────────────────────
async def _agent(*, system_prompt: str = "BASE prompt", tools_allowed=None) -> str:
    async with SessionLocal() as db:
        a = Agent(
            name="Harness", provider="openai", model="reg-model",
            system_prompt=system_prompt, tools_allowed=tools_allowed or [],
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return a.id


async def _make_eval_run(agent_id: str, cases: list[dict]) -> str:
    async with SessionLocal() as db:
        run = AgentEvalRun(agent_id=agent_id, status="done", case_count=len(cases))
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
        for c in cases:
            db.add(
                AgentEvalCase(
                    run_id=run_id,
                    prompt=c.get("prompt", "p"),
                    expected_outcome=c.get("expected_outcome"),
                    optimal_steps=c.get("optimal_steps"),
                    forbidden_tools=c.get("forbidden_tools", []),
                    held_out=c.get("held_out", False),
                    resolved=c.get("resolved"),
                    redundant_tool_calls=c.get("redundant_tool_calls", 0),
                    forbidden_tool_used=c.get("forbidden_tool_used", False),
                    parameter_hallucination=c.get("parameter_hallucination", False),
                )
            )
        await db.commit()
        return run_id


async def _make_report(agent_id: str, run_id: str, example_case_ids=None) -> str:
    async with SessionLocal() as db:
        w = WeaknessReport(
            agent_id=agent_id, agent_eval_run_id=run_id,
            pattern="agent forgets to check X first", example_case_ids=example_case_ids or [],
        )
        db.add(w)
        await db.commit()
        await db.refresh(w)
        return w.id


class FakeMinerJudge:
    def __init__(self, *a, **k):
        pass

    async def complete_json(self, system, user, **_kw):
        return {"pattern": "agent forgets to check X first"}


def _proposal_judge(field: str, new_value, rationale: str = "fix it"):
    class _J:
        def __init__(self, *a, **k):
            pass

        async def complete_json(self, system, user, **_kw):
            return {"field": field, "new_value": new_value, "rationale": rationale}

    return _J


def _regression_provider(regress_held_out: bool):
    class _P:
        def __init__(self, model):
            self.model = model

        async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
            system_text = messages[0]["content"] if messages else ""
            patched = "PATCHED" in str(system_text)
            user_text = messages[-1]["content"] if messages else ""
            is_out = "CASE-OUT" in str(user_text)
            ok = not (patched and is_out and regress_held_out)
            yield TextDelta("PASS" if ok else "FAIL")
            yield DoneEvent(finish_reason="stop")

    return _P


class FakeRegressionJudge:
    def __init__(self, *a, **k):
        pass

    async def complete_json(self, system, user, **_kw):
        return {"resolved": "PASS" in user}


def _patch_run_turn_env(monkeypatch, regress_held_out: bool) -> None:
    monkeypatch.setattr(
        "app.core.agent_runtime.get_provider", lambda n: _regression_provider(regress_held_out)(n)
    )
    monkeypatch.setattr("app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True)
    monkeypatch.setattr("app.harness.regression.Judge", FakeRegressionJudge)


# ── 17a Weakness Miner ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mine_creates_one_report_from_failing_cases(auth_client, monkeypatch):
    monkeypatch.setattr("app.harness.miner.Judge", FakeMinerJudge)
    aid = await _agent()
    run_id = await _make_eval_run(
        aid,
        [
            {"prompt": "p1", "resolved": False},
            {"prompt": "p2", "resolved": False},
            {"prompt": "p3", "resolved": False},
            {"prompt": "p4", "resolved": True},
            {"prompt": "p5", "resolved": False, "held_out": True},
        ],
    )
    report = await mine(run_id)
    assert report is not None
    assert report.agent_id == aid
    assert len(report.example_case_ids) == 3

    async with SessionLocal() as db:
        held_out_case = (
            await db.exec(select(AgentEvalCase).where(AgentEvalCase.prompt == "p5"))
        ).first()
    assert held_out_case.id not in report.example_case_ids


@pytest.mark.asyncio
async def test_mine_returns_none_below_min_failures(auth_client, monkeypatch):
    monkeypatch.setattr("app.harness.miner.Judge", FakeMinerJudge)
    aid = await _agent()
    run_id = await _make_eval_run(
        aid, [{"prompt": "p1", "resolved": False}, {"prompt": "p2", "resolved": True}]
    )
    assert await mine(run_id) is None
    async with SessionLocal() as db:
        assert (await db.exec(select(WeaknessReport))).first() is None


# ── 17b Harness Proposal ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_propose_clamps_tool_expansion(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.harness.proposal.Judge",
        _proposal_judge("tools_allowed", ["read_file", "execute_code", "sneaky_new_tool"]),
    )
    aid = await _agent(tools_allowed=["read_file", "execute_code"])
    run_id = await _make_eval_run(
        aid, [{"prompt": f"p{i}", "resolved": False} for i in range(3)]
    )
    report_id = await _make_report(aid, run_id)

    version = await propose(report_id)
    assert version is not None
    assert version.status == "proposed"
    assert version.diff == {"tools_allowed": ["read_file", "execute_code"]}
    assert version.config_snapshot["tools_allowed"] == ["read_file", "execute_code"]


@pytest.mark.asyncio
async def test_propose_rejects_system_prompt_add_via_new_tool_key_in_policy(auth_client, monkeypatch):
    """tool_policy diffs may only touch tools the agent already knows about."""
    monkeypatch.setattr(
        "app.harness.proposal.Judge",
        _proposal_judge("tool_policy", {"read_file": "auto", "delete_file": "auto"}),
    )
    aid = await _agent(tools_allowed=["read_file"])
    run_id = await _make_eval_run(
        aid, [{"prompt": f"p{i}", "resolved": False} for i in range(3)]
    )
    report_id = await _make_report(aid, run_id)

    version = await propose(report_id)
    assert version is not None
    assert version.diff == {"tool_policy": {"read_file": "auto"}}


@pytest.mark.asyncio
async def test_propose_locks_per_agent(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.harness.proposal.Judge", _proposal_judge("tools_allowed", ["read_file"])
    )
    aid = await _agent(tools_allowed=["read_file"])
    run_id = await _make_eval_run(
        aid, [{"prompt": f"p{i}", "resolved": False} for i in range(3)]
    )
    report1 = await _make_report(aid, run_id)
    report2 = await _make_report(aid, run_id)

    v1 = await propose(report1)
    assert v1 is not None
    v2 = await propose(report2)
    assert v2 is None

    async with SessionLocal() as db:
        rows = (
            await db.exec(select(AgentConfigVersion).where(AgentConfigVersion.agent_id == aid))
        ).all()
    assert len(rows) == 1


# ── 17c Proposal Validation (regression gate) ─────────────────────────────
async def _seed_candidate(
    aid: str, *, held_in_resolved: bool, held_out_resolved: bool, new_value: str
) -> str:
    run_id = await _make_eval_run(
        aid,
        [
            {"prompt": "solve CASE-IN", "resolved": held_in_resolved},
            {"prompt": "solve CASE-OUT", "resolved": held_out_resolved, "held_out": True},
        ],
    )
    report_id = await _make_report(aid, run_id)
    async with SessionLocal() as db:
        agent = await db.get(Agent, aid)
        snapshot = {
            "system_prompt": new_value,
            "tools_allowed": list(agent.tools_allowed or []),
            "tool_policy": dict(agent.tool_policy or {}),
            "orchestration": dict(agent.orchestration or {}),
        }
        v = AgentConfigVersion(
            agent_id=aid, weakness_report_id=report_id, source_eval_run_id=run_id,
            diff={"system_prompt": new_value}, config_snapshot=snapshot,
            rationale="test patch", status="proposed",
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return v.id


@pytest.mark.asyncio
async def test_validate_rejects_regression_on_held_out(auth_client, monkeypatch):
    _patch_run_turn_env(monkeypatch, regress_held_out=True)
    aid = await _agent(system_prompt="BASE prompt")
    version_id = await _seed_candidate(
        aid, held_in_resolved=False, held_out_resolved=True, new_value="PATCHED prompt"
    )

    await validate(version_id)

    async with SessionLocal() as db:
        v = await db.get(AgentConfigVersion, version_id)
        agent = await db.get(Agent, aid)
    assert v.status == "rejected"
    assert v.held_in_score == 1.0  # held-in case now passes
    assert v.held_out_score == 0.0  # but held-out regressed
    assert agent.system_prompt == "BASE prompt"  # live agent untouched


@pytest.mark.asyncio
async def test_validate_activates_good_patch_and_supersedes_prior(auth_client, monkeypatch):
    _patch_run_turn_env(monkeypatch, regress_held_out=False)
    aid = await _agent(system_prompt="BASE prompt")

    async with SessionLocal() as db:
        prior_active = AgentConfigVersion(
            agent_id=aid, diff={"system_prompt": "BASE prompt"},
            config_snapshot={"system_prompt": "BASE prompt", "tools_allowed": [],
                              "tool_policy": {}, "orchestration": {}},
            rationale="prior", status="active",
        )
        db.add(prior_active)
        await db.commit()
        await db.refresh(prior_active)
        prior_id = prior_active.id

    version_id = await _seed_candidate(
        aid, held_in_resolved=False, held_out_resolved=True, new_value="PATCHED prompt"
    )

    await validate(version_id)

    async with SessionLocal() as db:
        v = await db.get(AgentConfigVersion, version_id)
        prior = await db.get(AgentConfigVersion, prior_id)
        agent = await db.get(Agent, aid)
    assert v.status == "active"
    assert v.activated_at is not None
    assert v.held_in_score == 1.0
    assert v.held_out_score == 1.0
    assert prior.status == "superseded"
    assert agent.system_prompt == "PATCHED prompt"

    # the very next turn observably uses the new config, no redeploy needed
    captured: list[str] = []

    class _CaptureProvider:
        def __init__(self, model):
            self.model = model

        async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
            captured.append(messages[0]["content"])
            yield TextDelta("ok")
            yield DoneEvent(finish_reason="stop")

    monkeypatch.setattr("app.core.agent_runtime.get_provider", lambda n: _CaptureProvider(n))
    from app.core.agent_runtime import run_turn
    from app.db.models import ChatSession, Message

    async with SessionLocal() as db:
        uid = (await db.exec(select(User))).first().id
        sess = ChatSession(user_id=uid, agent_id=aid)
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        sid = sess.id
        db.add(Message(session_id=sid, role="user", content="hi"))
        await db.commit()

    await run_turn(sid, wait_for_approval=False, mode="unattended")
    assert any("PATCHED prompt" in c for c in captured)


@pytest.mark.asyncio
async def test_validate_rejects_without_held_out_cases(auth_client, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("run_turn should never be invoked without held-out cases")

    monkeypatch.setattr("app.core.agent_runtime.get_provider", _boom)
    aid = await _agent()
    run_id = await _make_eval_run(aid, [{"prompt": "solve CASE-IN", "resolved": False}])
    report_id = await _make_report(aid, run_id)
    async with SessionLocal() as db:
        v = AgentConfigVersion(
            agent_id=aid, weakness_report_id=report_id, source_eval_run_id=run_id,
            diff={"system_prompt": "PATCHED"}, config_snapshot={"system_prompt": "PATCHED"},
            rationale="x", status="proposed",
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        version_id = v.id

    await validate(version_id)

    async with SessionLocal() as db:
        v = await db.get(AgentConfigVersion, version_id)
    assert v.status == "rejected"
    assert v.reject_reason == "no_held_out_cases"


# ── rollback ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rollback_restores_full_snapshot_and_keeps_history(auth_client):
    aid = await _agent(system_prompt="B")
    async with SessionLocal() as db:
        v_a = AgentConfigVersion(
            agent_id=aid, diff={"system_prompt": "A"},
            config_snapshot={"system_prompt": "A", "tools_allowed": ["read_file"],
                              "tool_policy": {"read_file": "auto"}, "orchestration": {}},
            rationale="a", status="superseded",
        )
        v_b = AgentConfigVersion(
            agent_id=aid, diff={"system_prompt": "B"},
            config_snapshot={"system_prompt": "B", "tools_allowed": [],
                              "tool_policy": {}, "orchestration": {}},
            rationale="b", status="active",
        )
        db.add(v_a)
        db.add(v_b)
        await db.commit()
        await db.refresh(v_a)
        await db.refresh(v_b)

    async with SessionLocal() as db:
        result = await rollback(db, aid, v_a.id)
    assert result.status == "active"
    assert result.activated_at is not None

    async with SessionLocal() as db:
        agent = await db.get(Agent, aid)
        a_row = await db.get(AgentConfigVersion, v_a.id)
        b_row = await db.get(AgentConfigVersion, v_b.id)
    assert agent.system_prompt == "A"
    assert agent.tools_allowed == ["read_file"]
    assert agent.tool_policy == {"read_file": "auto"}
    assert a_row.status == "active"
    assert b_row.status == "superseded"
    # history preserved, nothing deleted
    async with SessionLocal() as db:
        rows = (
            await db.exec(select(AgentConfigVersion).where(AgentConfigVersion.agent_id == aid))
        ).all()
    assert len(rows) == 2


# ── REST integration ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_harness_run_endpoint_and_config_versions_api(auth_client, monkeypatch):
    monkeypatch.setattr("app.harness.miner.Judge", FakeMinerJudge)
    monkeypatch.setattr(
        "app.harness.proposal.Judge",
        _proposal_judge("system_prompt", "PATCHED prompt"),
    )
    _patch_run_turn_env(monkeypatch, regress_held_out=False)

    aid = await _agent(system_prompt="BASE prompt")
    run_id = await _make_eval_run(
        aid,
        [
            {"prompt": "solve CASE-IN", "resolved": False},
            {"prompt": "solve CASE-IN-2", "resolved": False},
            {"prompt": "solve CASE-IN-3", "resolved": False},
            {"prompt": "solve CASE-OUT", "resolved": True, "held_out": True},
        ],
    )

    r = await auth_client.post(f"/api/agent-eval/runs/{run_id}/harness/run")
    assert r.status_code == 202, r.text

    got = []
    for _ in range(100):
        await asyncio.sleep(0.1)
        got = (await auth_client.get(f"/api/agents/{aid}/config-versions")).json()
        if got and got[0]["status"] != "proposed":
            break
    assert got, "no config version was created"
    assert got[0]["status"] == "active", got

    reports = (await auth_client.get(f"/api/agents/{aid}/weakness-reports")).json()
    assert len(reports) == 1

    # not-yet-finished run -> 409
    async with SessionLocal() as db:
        running = AgentEvalRun(agent_id=aid, status="running")
        db.add(running)
        await db.commit()
        await db.refresh(running)
    bad = await auth_client.post(f"/api/agent-eval/runs/{running.id}/harness/run")
    assert bad.status_code == 409
