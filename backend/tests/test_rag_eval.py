"""RAG evaluation (Phase 12f) — RAGAS metrics, golden-set synth, run flow.

All LLM/embedding work goes through a FakeJudge so nothing hits the network.
"""

from __future__ import annotations

import hashlib
import json

import pytest
import pytest_asyncio

from app.rag import embeddings as emb_mod
from app.rag import ingest as ingest_mod
from app.rag.eval import ragas
from app.rag.eval.synth import generate

_DIM = 32


def _vec(text: str) -> list[float]:
    v = [0.0] * _DIM
    for w in text.lower().split():
        v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % _DIM] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class FakeJudge:
    """Duck-typed Judge: canned JSON keyed on the system prompt."""

    def __init__(self, **_kw):
        self.embed_model = "fake"

    async def complete(self, system: str, user: str, *, max_tokens: int = 800) -> str:
        s = system.lower()
        if "answer the question using only" in s:
            return "The refund window is 30 days." if "refund" in user.lower() else "See the context."
        if "factual qa pair" in s:
            return json.dumps({"question": "What is covered?", "answer": user[:120]})
        if "harder qa pair" in s:
            return json.dumps({"question": "Combined question?", "answer": "combined answer"})
        if "not answered by the given text" in s:
            return json.dumps({"questions": ["What is the CEO's salary?", "Where is the datacenter?"]})
        if "strict qa-set reviewer" in s:
            return json.dumps({"keep": True, "reason": "clear"})
        if "grounded in the given context" in s:
            return json.dumps({"statements": [
                {"text": "claim A", "supported": True},
                {"text": "claim B", "supported": False},
            ]})
        if "reverse-engineer the question" in s:
            return json.dumps({"questions": ["What is the refund window?", "How long to return?"]})
        if "each context chunk helps" in s:
            return json.dumps({"relevant": [True, False, True]})
        if "reference answer is covered" in s:
            return json.dumps({"statements": [
                {"text": "s1", "attributed": True},
                {"text": "s2", "attributed": True},
            ]})
        return "{}"

    async def complete_json(self, system: str, user: str, *, max_tokens: int = 800):
        return json.loads(await self.complete(system, user, max_tokens=max_tokens))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vec(t) for t in texts]


# ── RAGAS metrics ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_faithfulness_is_supported_over_total():
    r = await ragas.faithfulness(FakeJudge(), answer="a b", contexts=["ctx"])
    assert r.score == 0.5
    assert r.rationale["supported"] == 1 and r.rationale["total"] == 2


@pytest.mark.asyncio
async def test_answer_relevancy_uses_reverse_questions():
    r = await ragas.answer_relevancy(
        FakeJudge(), question="What is the refund window?", answer="30 days"
    )
    assert r.score is not None and 0.0 <= r.score <= 1.0
    assert len(r.rationale["generated"]) == 2


@pytest.mark.asyncio
async def test_context_precision_average_precision():
    # relevant flags [T, F, T] -> AP = (1/1 + 2/3) / 2
    r = await ragas.context_precision(
        FakeJudge(), question="q", contexts=["c0", "c1", "c2"], ground_truth="gt"
    )
    assert r.score == pytest.approx((1.0 + 2 / 3) / 2, abs=1e-3)


@pytest.mark.asyncio
async def test_context_recall_needs_ground_truth():
    none_r = await ragas.context_recall(FakeJudge(), contexts=["c"], ground_truth=None)
    assert none_r.score is None
    r = await ragas.context_recall(FakeJudge(), contexts=["c"], ground_truth="the answer")
    assert r.score == 1.0


@pytest.mark.asyncio
async def test_score_all_shape():
    out = await ragas.score_all(
        FakeJudge(), question="q", answer="a b", contexts=["c0", "c1"], ground_truth="gt",
    )
    for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        assert k in out
    assert "faithfulness" in out["rationale"]


# ── synth ────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def _fake_ingest_embeddings(monkeypatch):
    async def fake_embed_texts(model, texts, **kw):
        return [_vec(t) for t in texts]

    async def fake_embed_query(model, text, **kw):
        return _vec(text)

    monkeypatch.setattr(emb_mod, "embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.rag.ingest.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.rag.pipeline.embed_query", fake_embed_query)
    monkeypatch.setattr("app.rag.pipeline.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.api.routes_rag._schedule_ingest", lambda _d: None)
    yield


async def _mk_collection(ac, name="ekb"):
    r = await ac.post("/api/kb/collections", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add(ac, cid, title, text):
    r = await ac.post(f"/api/kb/collections/{cid}/documents/from-text",
                      json={"title": title, "text": text})
    await ingest_mod.run(r.json()["id"])


@pytest.mark.asyncio
async def test_synth_generates_cases_with_negatives():
    from app.rag.eval import synth

    # generate() reads chunks from the DB; seed a couple via the flow fixtures
    # is heavier — instead stub _sample_chunks with fake chunk objects.
    class _C:
        def __init__(self, t, d):
            self.text, self.document_id = t, d

    async def fake_sample(_cid, _k):
        return [_C(f"Passage {i} about widgets and gears numbered {i}.", f"d{i % 2}") for i in range(6)]

    import app.rag.eval.synth as synth_mod

    synth_mod._sample_chunks = fake_sample  # type: ignore
    cases = await generate("cid", n=12, judge=FakeJudge())
    assert 1 <= len(cases) <= 12
    assert any(c["ground_truth"] == synth.__dict__["_NEG_GT"] for c in cases)
    assert all(c["question"] and c["ground_truth"] for c in cases)


# ── run flow ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_eval_run_end_to_end(auth_client, monkeypatch):
    monkeypatch.setattr("app.rag.eval.runner.Judge", FakeJudge)

    ac = auth_client
    cid = await _mk_collection(ac)
    await _add(ac, cid, "Refunds", "Our refund policy allows returns within 30 days of purchase.")
    await _add(ac, cid, "Shipping", "Standard shipping takes five to seven business days.")

    body = {
        "collection_id": cid,
        "name": "baseline",
        "config": {"rerank_mode": "none"},
        "cases": [
            {"question": "How long is the refund window?", "ground_truth": "30 days"},
            {"question": "What colour is the sky on Mars?", "ground_truth":
             "The documents do not contain this information."},
        ],
    }
    r = await ac.post("/api/kb/eval-runs", json=body)
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert r.json()["status"] == "running"
    assert r.json()["config_snapshot"]["rerank_mode"] == "none"

    # runner ran as a background task on the same loop — let it finish
    for _ in range(50):
        got = (await ac.get(f"/api/kb/eval-runs/{run_id}")).json()
        if got["run"]["status"] == "done":
            break
        await _sleep()
    assert got["run"]["status"] == "done"
    assert got["run"]["faithfulness"] is not None
    assert len(got["cases"]) == 2
    c0 = next(c for c in got["cases"] if "refund" in c["question"].lower())
    assert c0["answer"]
    assert c0["contexts"]

    # listed + deletable
    listing = (await ac.get(f"/api/kb/eval-runs?collection_id={cid}")).json()
    assert any(x["id"] == run_id for x in listing)
    assert (await ac.delete(f"/api/kb/eval-runs/{run_id}")).status_code == 204


async def _sleep():
    import asyncio

    await asyncio.sleep(0.05)
