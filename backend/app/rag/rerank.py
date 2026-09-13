"""Re-rank fused candidates down to ``rerank_top_n`` (SPEC §14.2 group 3).

    none          — keep fusion order
    mmr           — Maximal Marginal Relevance over the dense embeddings
                    (relevance vs. diversity, tuned by ``mmr_lambda``)
    llm           — ask a model to order the passages by relevance
    cross_encoder — sentence-transformers cross-encoder (optional, ~2GB); if the
                    package isn't installed it degrades to llm, then mmr, then none

Re-rank never *adds* candidates — worst case it returns the top-N of what it got.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import structlog

log = structlog.get_logger("pagi.rag.rerank")


@dataclass
class RerankCand:
    chunk_id: str
    text: str
    score: float
    embedding: list[float] | None = None


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _mmr(
    query_vec: list[float], cands: list[RerankCand], top_n: int, lam: float
) -> list[tuple[str, float]]:
    pool = [c for c in cands if c.embedding]
    if not pool or not query_vec:
        return [(c.chunk_id, c.score) for c in cands[:top_n]]
    rel = {c.chunk_id: _cos(query_vec, c.embedding) for c in pool}
    picked: list[RerankCand] = []
    remaining = list(pool)
    while remaining and len(picked) < top_n:
        best, best_val = None, -1e9
        for c in remaining:
            div = max((_cos(c.embedding, p.embedding) for p in picked), default=0.0)
            val = lam * rel[c.chunk_id] - (1 - lam) * div
            if val > best_val:
                best, best_val = c, val
        picked.append(best)
        remaining.remove(best)
    return [(c.chunk_id, rel[c.chunk_id]) for c in picked]


async def _llm_rank(
    query: str, cands: list[RerankCand], top_n: int, model: str, provider_name: str | None,
    session_id: str | None,
) -> list[tuple[str, float]] | None:
    from ..providers import DoneEvent, TextDelta, UsageEvent, get_provider

    # any adapter just forwards the model string to litellm; use the agent's
    # provider when known, else the openai adapter (prefix-free passthrough).
    try:
        provider = get_provider(provider_name or "openai")
    except Exception:
        provider = get_provider("openai")

    numbered = "\n\n".join(f"[{i}] {c.text[:1200]}" for i, c in enumerate(cands))
    instruction = (
        "You are a search re-ranker. Given a query and numbered passages, return "
        f"the {top_n} most relevant passage numbers, most relevant first, as a "
        "comma-separated list of integers only (e.g. `3,0,7`). No prose."
    )
    parts: list[str] = []
    try:
        async for ev in provider.stream_chat(
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": f"Query: {query}\n\nPassages:\n{numbered}"},
            ],
            tools=[],
            model=model,
            temperature=0.0,
            max_tokens=64,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, (DoneEvent, UsageEvent)):
                continue
    except Exception as exc:  # pragma: no cover - external
        log.warning("llm_rerank_failed", error=str(exc))
        return None

    raw = "".join(parts)
    order = [int(n) for n in re.findall(r"\d+", raw) if int(n) < len(cands)]
    seen: set[int] = set()
    order = [i for i in order if not (i in seen or seen.add(i))]
    if not order:
        return None
    # score by descending position so packer ordering stays meaningful
    ranked = [(cands[i].chunk_id, float(len(order) - pos)) for pos, i in enumerate(order)]
    # append any not mentioned, after
    mentioned = set(order)
    for i, c in enumerate(cands):
        if i not in mentioned:
            ranked.append((c.chunk_id, 0.0))
    return ranked[:top_n]


def _cross_encoder(
    query: str, cands: list[RerankCand], top_n: int, model: str | None
) -> list[tuple[str, float]] | None:
    try:
        from sentence_transformers import CrossEncoder
    except Exception:  # pragma: no cover - optional heavy dep
        log.info("cross_encoder_missing", detail="falling back")
        return None
    name = model or "cross-encoder/ms-marco-MiniLM-L-6-v2"
    try:
        ce = CrossEncoder(name)
        scores = ce.predict([(query, c.text) for c in cands])
    except Exception as exc:  # pragma: no cover
        log.warning("cross_encoder_failed", error=str(exc))
        return None
    ranked = sorted(zip((c.chunk_id for c in cands), (float(s) for s in scores)),
                    key=lambda t: t[1], reverse=True)
    return ranked[:top_n]


async def rerank(
    query: str,
    candidates: list[RerankCand],
    cfg: dict,
    *,
    query_vec: list[float] | None = None,
    model: str | None = None,
    provider: str | None = None,
    session_id: str | None = None,
) -> tuple[list[tuple[str, float]], str]:
    """Return (ranked (chunk_id, score) truncated to rerank_top_n, mode_used)."""
    mode = cfg.get("rerank_mode") or "llm"
    top_n = int(cfg.get("rerank_top_n") or 5)
    if not candidates:
        return [], "none"

    if mode == "none":
        return [(c.chunk_id, c.score) for c in candidates[:top_n]], "none"

    if mode == "mmr":
        lam = float(cfg.get("mmr_lambda") if cfg.get("mmr_lambda") is not None else 0.5)
        return _mmr(query_vec or [], candidates, top_n, lam), "mmr"

    if mode == "cross_encoder":
        ce = _cross_encoder(query, candidates, top_n, cfg.get("rerank_model"))
        if ce is not None:
            return ce, "cross_encoder"
        mode = "llm"  # fall through

    if mode == "llm":
        chosen_model = cfg.get("rerank_model") or model
        if chosen_model:
            out = await _llm_rank(query, candidates, top_n, chosen_model, provider, session_id)
            if out is not None:
                return out, "llm"
        # no model, or llm failed → MMR if we have vectors, else passthrough
        if query_vec:
            lam = float(cfg.get("mmr_lambda") if cfg.get("mmr_lambda") is not None else 0.5)
            return _mmr(query_vec, candidates, top_n, lam), "mmr(fallback)"
        return [(c.chunk_id, c.score) for c in candidates[:top_n]], "none(fallback)"

    return [(c.chunk_id, c.score) for c in candidates[:top_n]], "none"
