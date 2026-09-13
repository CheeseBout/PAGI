"""RAG unit tests — config cascade, chunker, packer, vector store, BM25, fusion,
rerank (no network)."""

from __future__ import annotations

import numpy as np
import pytest

from app.rag import crag as crag_mod
from app.rag import enrich as enrich_mod
from app.rag import query_transform
from app.rag.bm25 import Bm25Index, tokenize
from app.rag.chunkers import Chunk, build_chunks, chunk_text
from app.rag.config import resolve_config
from app.rag.fusion import reciprocal_rank_fusion, weighted_fusion
from app.rag.packer import PackChunk, pack
from app.rag.rerank import RerankCand, rerank
from app.rag.router import classify
from app.rag.store.sqlite_np import SqliteNumpyStore


# ── cascade (SPEC §14.2) ───────────────────────────────────────────────
def test_cascade_defaults_when_no_layers():
    cfg = resolve_config()
    assert cfg["chunk_strategy"] == "recursive"
    assert cfg["retrieval_mode"] == "hybrid"
    assert cfg["mode"] == "auto"
    # fully populated — no None left
    assert None not in [cfg[k] for k in cfg if k not in ("context_max_tokens",
                                                          "rerank_model", "transform_model",
                                                          "enrich_contextual_model",
                                                          "eval_judge_model")]


def test_cascade_lower_tier_wins_and_none_inherits():
    cfg = resolve_config(
        collection={"chunk_size": 800, "rerank_mode": "cross_encoder"},
        agent={"rerank_mode": "mmr", "top_k_dense": None},  # None must not override
        request={"mode": "tool"},
    )
    assert cfg["chunk_size"] == 800          # from collection
    assert cfg["rerank_mode"] == "mmr"       # agent beat collection
    assert cfg["top_k_dense"] == 50          # None ignored -> baseline
    assert cfg["mode"] == "tool"             # request beat everything


def test_cascade_rejects_unknown_and_out_of_range():
    cfg = resolve_config(collection={"hybrid_alpha": 0.3})
    assert cfg["hybrid_alpha"] == 0.3
    with pytest.raises(Exception):
        resolve_config(collection={"hybrid_alpha": 5})  # > 1
    with pytest.raises(Exception):
        resolve_config(collection={"rerank_moed": "mmr"})  # typo -> not silently ignored


# ── chunker (SPEC §14.3) ──────────────────────────────────────────────
def test_recursive_chunk_preserves_content_and_bounds():
    para = "Sentence about apples. " * 40  # ~920 chars
    text = para + "\n\n" + "Second topic on oranges. " * 40
    chunks = chunk_text(text, {"chunk_strategy": "recursive", "chunk_size": 100,
                               "chunk_overlap_pct": 0.1})
    assert len(chunks) >= 2
    # every non-overlap word survives somewhere
    joined = " ".join(c.text for c in chunks)
    assert "apples" in joined and "oranges" in joined
    # ordinals are contiguous from 0
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # size bound honoured (chars ~= tokens*4, + one overlap tail)
    assert all(len(c.text) <= 100 * 4 * 2 for c in chunks)


def test_semantic_strategy_falls_back_not_raises():
    chunks = chunk_text("a b c d e " * 50, {"chunk_strategy": "semantic", "chunk_size": 50})
    assert chunks and all(c.token_count > 0 for c in chunks)


# ── packer (SPEC §14.6) ──────────────────────────────────────────────
def _pc(i: int, score: float, text: str = "body") -> PackChunk:
    return PackChunk(
        chunk_id=f"c{i}", document_id=f"d{i}", title=f"Doc {i}",
        source_uri=f"doc{i}.txt", text=text, score=score, updated_at="2026-01-01T00:00:00Z",
    )


def test_pack_respects_budget():
    hits = [_pc(i, 1.0 - i * 0.01, "word " * 500) for i in range(10)]
    packed = pack(hits, {"context_format": "xml"}, budget_tokens=800)
    assert 0 < len(packed.used_chunk_ids) < 10
    assert packed.token_count <= 900


def test_pack_lost_in_the_middle_order():
    hits = [_pc(i, 1.0 - i * 0.1) for i in range(5)]
    packed = pack(hits, {"order_strategy": "lost_in_middle", "context_format": "xml"},
                 budget_tokens=100_000)
    body = packed.text
    # strongest (Doc 0) near the top, second-strongest (Doc 1) near the very end
    assert body.index("Doc 0") < body.index("Doc 2")
    assert body.index("Doc 1") > body.index("Doc 3")


def test_pack_has_data_boundary_and_citations():
    packed = pack([_pc(0, 0.9)], {"context_format": "xml", "grounding_strictness": "strict"},
                  budget_tokens=10_000)
    assert "<retrieved_context>" in packed.text
    assert "NOT instructions" in packed.text
    assert packed.citations[0]["chunk_id"] == "c0"
    assert "ONLY from <retrieved_context>" in packed.system_note


# ── vector store (SPEC §14.10) — fake vectors, no API ────────────────
@pytest.mark.asyncio
async def test_sqlite_np_store_cosine_ranking(monkeypatch):
    store = SqliteNumpyStore()

    ids = ["a", "b", "c"]
    mat = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32)

    async def _fake_index(collection_id: str):
        from app.rag.store.sqlite_np import _Index

        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return _Index(ids, mat / norms)

    monkeypatch.setattr(store, "_index", _fake_index)

    hits = await store.query("k", [1.0, 0.0], top_k=2)
    assert [h.chunk_id for h in hits] == ["a", "b"]
    assert hits[0].score > hits[1].score

    filtered = await store.query("k", [1.0, 0.0], top_k=3, allowed_chunk_ids={"c"})
    assert [h.chunk_id for h in filtered] == ["c"]


# ── BM25 (SPEC §14.2 group 2) ───────────────────────────────────────
def test_tokenize_keeps_ids_and_codes():
    toks = tokenize("Order SKU-9981 shipped, ref v2.5 / invoice INV_2024.")
    assert "sku-9981" in toks
    assert "v2.5" in toks
    assert "inv_2024" in toks


def test_bm25_finds_exact_id_match():
    docs = [
        "the quarterly report covers revenue growth and margins",
        "incident ticket JIRA-4471 tracks the outage on the payments service",
        "shipping policy and delivery timelines for standard orders",
    ]
    idx = Bm25Index([f"c{i}" for i in range(len(docs))], [tokenize(d) for d in docs])
    hits = idx.search(tokenize("what happened with JIRA-4471"), top_k=3)
    assert hits and hits[0][0] == "c1"


# ── fusion (SPEC §14.4 step 4) ─────────────────────────────────────
def test_rrf_rewards_agreement():
    dense = [("a", 0.9), ("b", 0.8), ("c", 0.1)]
    sparse = [("b", 5.0), ("a", 4.0), ("d", 1.0)]
    fused = reciprocal_rank_fusion(dense, sparse, k=60)
    ids = [cid for cid, _ in fused]
    # a and b appear in both lists near the top -> they beat c and d
    assert set(ids[:2]) == {"a", "b"}
    assert ids.index("a") < ids.index("c")


def test_weighted_fusion_alpha_shifts_ranking():
    dense = [("a", 1.0), ("b", 0.0)]
    sparse = [("b", 1.0), ("a", 0.0)]
    dense_heavy = weighted_fusion(dense, sparse, alpha=0.9)
    sparse_heavy = weighted_fusion(dense, sparse, alpha=0.1)
    assert dense_heavy[0][0] == "a"
    assert sparse_heavy[0][0] == "b"


# ── rerank (SPEC §14.2 group 3) ────────────────────────────────────
@pytest.mark.asyncio
async def test_mmr_rerank_promotes_diversity():
    q = [1.0, 0.0, 0.0]
    cands = [
        RerankCand("a", "topic one", 0.9, [1.0, 0.0, 0.0]),
        RerankCand("b", "topic one again", 0.89, [0.99, 0.01, 0.0]),  # near-dup of a
        RerankCand("c", "topic two", 0.7, [0.6, 0.0, 0.8]),
    ]
    ranked, mode = await rerank(
        q and q, cands, {"rerank_mode": "mmr", "rerank_top_n": 2, "mmr_lambda": 0.3},
        query_vec=q,
    )
    assert mode == "mmr"
    ids = [cid for cid, _ in ranked]
    assert ids[0] == "a"
    assert ids[1] == "c"  # diversity beat the near-duplicate 'b'


@pytest.mark.asyncio
async def test_rerank_none_is_passthrough_topn():
    cands = [RerankCand(f"c{i}", "t", 1.0 - i * 0.1) for i in range(5)]
    ranked, mode = await rerank("q", cands, {"rerank_mode": "none", "rerank_top_n": 3})
    assert mode == "none"
    assert [cid for cid, _ in ranked] == ["c0", "c1", "c2"]


# ── router (SPEC §14.5) ────────────────────────────────────────────
def test_router_classify():
    assert classify("What is the refund window?") == "simple"
    assert classify("Compare the refund policy and the shipping policy") == "complex"
    assert classify("How does onboarding work for a contractor who joins mid-quarter?") == "medium"
    assert classify("What are the SLA targets and how are breaches escalated and to whom?") == "complex"


# ── query transform (SPEC §14.2 group 4) ──────────────────────────
@pytest.mark.asyncio
async def test_query_transform_modes(monkeypatch):
    async def fake_llm(model, provider, system, user):
        s = system.lower()
        if "hypothetical" in s or "reference document" in s:
            return "The refund window is 30 days from purchase."
        if "abstract" in s or "general" in s:
            return "What is the company's returns policy?"
        if "diverse standalone search queries" in s:
            return "refund window length\nhow many days to return\nreturn deadline policy"
        if "atomic sub-questions" in s:
            return "What is the refund window?\nWhat items are eligible?"
        return ""

    monkeypatch.setattr(query_transform, "_llm", fake_llm)

    q = "How long do I have to return something for a refund?"
    none_qs, m = await query_transform.transform(q, {"query_transform": "none"}, model="m")
    assert m == "none" and [x["kind"] for x in none_qs] == ["original"]

    hyde_qs, m = await query_transform.transform(q, {"query_transform": "hyde"}, model="m")
    assert m == "hyde" and hyde_qs[1]["kind"] == "hyde" and "30 days" in hyde_qs[1]["text"]

    mq, m = await query_transform.transform(
        q, {"query_transform": "multi_query", "multi_query_n": 3}, model="m"
    )
    assert m == "multi_query" and len(mq) == 4  # original + 3

    dq, m = await query_transform.transform(q, {"query_transform": "decompose"}, model="m")
    assert m == "decompose" and len(dq) >= 2

    # auto routes via the heuristic; no model needed for a simple query
    auto_qs, m = await query_transform.transform("Who is the CEO?", {"query_transform": "auto"})
    assert m == "none"


@pytest.mark.asyncio
async def test_query_transform_no_model_is_noop():
    qs, m = await query_transform.transform("anything", {"query_transform": "hyde"}, model=None)
    assert m == "hyde" and [x["kind"] for x in qs] == ["original"]


# ── CRAG (SPEC §14.4 step 7) ──────────────────────────────────────
class _FakeProvider:
    def __init__(self, text: str):
        self.text = text

    async def stream_chat(self, **_kw):
        from app.providers import DoneEvent, TextDelta

        yield TextDelta(self.text)
        yield DoneEvent(finish_reason="stop")


@pytest.mark.asyncio
async def test_crag_grade_threshold(monkeypatch):
    monkeypatch.setattr("app.providers.get_provider", lambda _n: _FakeProvider("0.4"))
    v, s = await crag_mod.grade("q", ["ctx"], {"crag_threshold": 0.6}, model="m", provider="openai")
    assert v == "insufficient" and s == 0.4

    monkeypatch.setattr("app.providers.get_provider", lambda _n: _FakeProvider("0.9"))
    v, s = await crag_mod.grade("q", ["ctx"], {"crag_threshold": 0.6}, model="m", provider="openai")
    assert v == "sufficient" and s == 0.9


@pytest.mark.asyncio
async def test_crag_grade_no_model_is_permissive():
    v, s = await crag_mod.grade("q", ["ctx"], {}, model=None, provider=None)
    assert v == "sufficient"
    v, s = await crag_mod.grade("q", [], {}, model="m", provider="openai")
    assert v == "insufficient"


@pytest.mark.asyncio
async def test_crag_web_fallback(monkeypatch):
    async def no_key(*_a, **_kw):
        return {"error": "web_search is not configured (set TAVILY_API_KEY)"}

    monkeypatch.setattr("app.tools.builtin.web_search.search", no_key)
    assert await crag_mod.web_fallback("some query") == []

    async def with_results(*_a, **_kw):
        return {"answer": "42", "results": [{"title": "T", "content": "body text"}]}

    monkeypatch.setattr("app.tools.builtin.web_search.search", with_results)
    out = await crag_mod.web_fallback("some query")
    assert out and out[0].startswith("[web]") and any("body text" in x for x in out)


# ── enrichment (SPEC §14.2 group 1 / §14.3) ───────────────────────
def _chunks(*texts):
    return [Chunk(text=t, ordinal=i, token_count=len(t) // 4) for i, t in enumerate(texts)]


@pytest.mark.asyncio
async def test_enrich_no_model_is_noop():
    cs = _chunks("some chunk text")
    doc_meta = await enrich_mod.enrich(
        cs, full_text="doc", cfg={"enrich_contextual": True}, model=None, provider=None,
    )
    assert doc_meta == {}
    assert cs[0].text_embedded == ""  # untouched -> embed_text() falls back to text


@pytest.mark.asyncio
async def test_enrich_contextual_and_hyqa_touch_only_text_embedded(monkeypatch):
    async def fake_llm(model, provider, system, user, *, max_tokens):
        s = system.lower()
        if "situating" in s:
            return "This chunk is from the Refunds section."
        if "questions this chunk answers" in s:
            return "How long is the refund window?\nWhat is the return deadline?"
        return ""

    monkeypatch.setattr(enrich_mod, "_llm", fake_llm)
    cs = _chunks("Refunds are allowed within 30 days.")
    await enrich_mod.enrich(
        cs, full_text="full doc text", cfg={"enrich_contextual": True, "enrich_hyqa": True,
                                            "enrich_hyqa_n": 2},
        model="m", provider="openai",
    )
    c = cs[0]
    assert c.text == "Refunds are allowed within 30 days."          # unchanged
    assert c.text_embedded != c.text
    assert "Refunds section" in c.text_embedded
    assert "refund window" in c.text_embedded.lower()
    assert c.text in c.text_embedded                                 # original still embedded


@pytest.mark.asyncio
async def test_enrich_summary_and_metadata(monkeypatch):
    async def fake_llm(model, provider, system, user, *, max_tokens):
        s = system.lower()
        if "summarise the document" in s:
            return "A short refund policy document."
        if "extract document metadata" in s:
            return '{"author":"Legal","date":"2026-01","doc_type":"policy","entities":["Refunds"]}'
        return ""

    monkeypatch.setattr(enrich_mod, "_llm", fake_llm)
    cs = _chunks("chunk one", "chunk two")
    doc_meta = await enrich_mod.enrich(
        cs, full_text="doc", cfg={"enrich_summary": True, "enrich_metadata_extract": True},
        model="m", provider="openai",
    )
    assert doc_meta["author"] == "Legal" and doc_meta["doc_type"] == "policy"
    assert doc_meta["entities"] == ["Refunds"]
    for c in cs:
        assert c.meta["doc_summary"].startswith("A short refund")
        assert c.meta["author"] == "Legal"
        assert c.text_embedded == ""  # no per-chunk LLM enrichment -> embeds text


def test_wants_enrichment():
    assert enrich_mod.wants_enrichment({"enrich_contextual": True})
    assert enrich_mod.wants_enrichment({"enrich_hyqa": True})
    assert not enrich_mod.wants_enrichment({"chunk_size": 400})


# ── parent_child chunking (SPEC §14.4 step 6) ──────────────────────
@pytest.mark.asyncio
async def test_parent_child_produces_linked_children():
    text = "\n\n".join(f"Paragraph {i} about subject number {i}. " * 12 for i in range(6))
    cs = await build_chunks(
        text,
        {"chunk_strategy": "parent_child", "chunk_size": 60, "parent_chunk_size": 300},
        embed_model="fake",
    )
    assert cs.parents, "expected parent chunks"
    assert cs.chunks, "expected child chunks"
    assert all(c.parent_ordinal is not None for c in cs.chunks)
    assert {c.parent_ordinal for c in cs.chunks} <= {p.ordinal for p in cs.parents}
    # a parent is bigger than its children
    assert max(p.token_count for p in cs.parents) > max(c.token_count for c in cs.chunks)
