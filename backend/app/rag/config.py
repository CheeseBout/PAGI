"""``RagConfig`` + the 4-tier cascade resolver (SPEC §14.2).

Every field is Optional: ``None`` means "inherit from the tier above", never
"disabled". The cascade is::

    env (RAG_DEFAULT_CONFIG_JSON + the defaults below)
      -> kb_collections.config
      -> agents.rag_config
      -> per-request override (Playground / eval)

Merge is *shallow* per field — a field set at a lower tier wins outright.
``resolve_config`` returns a fully-populated dict (no ``None``) and is snapshotted
verbatim into ``kb_query_logs.config_snapshot``.

Phase 12a uses a subset (chunking=recursive, retrieval=dense, packing=xml). The
rest of the schema is defined now so the cascade, the API and the stored
snapshots are stable; 12b–12d switch behaviour on without schema churn.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import get_settings


class RagConfig(BaseModel):
    """All fields Optional — see module docstring. Defaults live in ``_BASELINE``."""

    model_config = {"extra": "forbid"}

    # ── group 5: mode & generation (needed first — decides whether we retrieve)
    mode: Literal["off", "always", "tool", "auto"] | None = None
    context_budget_pct: float | None = Field(default=None, ge=0, le=0.8)
    context_max_tokens: int | None = Field(default=None, ge=0)
    context_format: Literal["xml", "markdown"] | None = None
    order_strategy: Literal["score_desc", "lost_in_middle"] | None = None
    grounding_strictness: Literal["off", "balanced", "strict"] | None = None
    include_citations: bool | None = None
    include_doc_dates: bool | None = None

    # ── group 1: indexing (🔁 only applied on ingest / re-ingest)
    chunk_strategy: Literal["fixed", "recursive", "semantic", "parent_child"] | None = None
    chunk_size: int | None = Field(default=None, ge=100, le=2000)
    chunk_overlap_pct: float | None = Field(default=None, ge=0, le=0.5)
    parent_chunk_size: int | None = Field(default=None, ge=200)
    semantic_breakpoint_percentile: int | None = Field(default=None, ge=50, le=99)
    enrich_contextual: bool | None = None
    enrich_contextual_model: str | None = None
    enrich_summary: bool | None = None
    enrich_hyqa: bool | None = None
    enrich_hyqa_n: int | None = Field(default=None, ge=1, le=5)
    enrich_metadata_extract: bool | None = None
    embedding_batch_size: int | None = Field(default=None, ge=1, le=512)

    # ── group 2: retrieval
    retrieval_mode: Literal["dense", "sparse", "hybrid"] | None = None
    top_k_dense: int | None = Field(default=None, ge=1, le=200)
    top_k_sparse: int | None = Field(default=None, ge=1, le=200)
    fusion: Literal["rrf", "weighted"] | None = None
    rrf_k: int | None = Field(default=None, ge=1)
    hybrid_alpha: float | None = Field(default=None, ge=0, le=1)
    score_threshold: float | None = Field(default=None)
    bm25_k1: float | None = None
    bm25_b: float | None = None
    vi_segmentation: bool | None = None
    metadata_filter: dict[str, Any] | None = None
    dedupe_similarity: float | None = Field(default=None, ge=0, le=1)

    # ── group 3: rerank
    rerank_mode: Literal["none", "llm", "cross_encoder", "mmr"] | None = None
    rerank_model: str | None = None
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    mmr_lambda: float | None = Field(default=None, ge=0, le=1)

    # ── group 4: query transformation & corrective
    query_transform: (
        Literal["none", "hyde", "multi_query", "step_back", "decompose", "auto"] | None
    ) = None
    multi_query_n: int | None = Field(default=None, ge=2, le=5)
    transform_model: str | None = None
    crag_enabled: bool | None = None
    crag_threshold: float | None = Field(default=None, ge=0, le=1)
    crag_fallback: Literal["none", "broaden", "web_search"] | None = None
    max_retrieval_rounds: int | None = Field(default=None, ge=1, le=5)

    # ── group 6: evaluation
    eval_judge_model: str | None = None
    faithfulness_min: float | None = None
    answer_relevancy_min: float | None = None
    context_precision_min: float | None = None
    context_recall_min: float | None = None
    eval_sample_rate: float | None = Field(default=None, ge=0, le=1)


# System-wide defaults (SPEC §14.2). Overridable per-field by env
# RAG_DEFAULT_CONFIG_JSON, then collection, then agent, then request.
_BASELINE: dict[str, Any] = {
    "mode": "auto",
    "context_budget_pct": 0.6,
    "context_max_tokens": None,
    "context_format": "xml",
    "order_strategy": "lost_in_middle",
    "grounding_strictness": "strict",
    "include_citations": True,
    "include_doc_dates": True,
    "chunk_strategy": "recursive",
    "chunk_size": 400,
    "chunk_overlap_pct": 0.12,
    "parent_chunk_size": 2000,
    "semantic_breakpoint_percentile": 95,
    "enrich_contextual": False,
    "enrich_contextual_model": None,
    "enrich_summary": False,
    "enrich_hyqa": False,
    "enrich_hyqa_n": 3,
    "enrich_metadata_extract": False,
    "embedding_batch_size": 64,
    "retrieval_mode": "hybrid",
    "top_k_dense": 50,
    "top_k_sparse": 50,
    "fusion": "rrf",
    "rrf_k": 60,
    "hybrid_alpha": 0.5,
    "score_threshold": 0.0,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "vi_segmentation": True,
    "metadata_filter": {},
    "dedupe_similarity": 0.97,
    "rerank_mode": "llm",
    "rerank_model": None,
    "rerank_top_n": 5,
    "mmr_lambda": 0.5,
    "query_transform": "none",
    "multi_query_n": 3,
    "transform_model": None,
    "crag_enabled": False,
    "crag_threshold": 0.6,
    "crag_fallback": "broaden",
    "max_retrieval_rounds": 2,
    "eval_judge_model": None,
    "faithfulness_min": 0.85,
    "answer_relevancy_min": 0.80,
    "context_precision_min": 0.70,
    "context_recall_min": 0.75,
    "eval_sample_rate": 0.0,
}


def _clean(layer: Any) -> dict[str, Any]:
    """Coerce a layer (dict / RagConfig / None) to a dict of only its set, non-None fields."""
    if layer is None:
        return {}
    if isinstance(layer, RagConfig):
        return {k: v for k, v in layer.model_dump(exclude_none=True).items()}
    if isinstance(layer, str):
        try:
            layer = json.loads(layer or "{}")
        except ValueError:
            return {}
    if not isinstance(layer, dict):
        return {}
    # validate + drop unknown / None
    parsed = RagConfig.model_validate({k: v for k, v in layer.items() if v is not None})
    return parsed.model_dump(exclude_none=True)


def _env_layer() -> dict[str, Any]:
    s = get_settings()
    merged = dict(_BASELINE)
    merged.update(_clean(s.rag_default_config_json))
    return merged


def resolve_config(
    *,
    collection: Any = None,
    agent: Any = None,
    request: Any = None,
) -> dict[str, Any]:
    """Return a fully-populated config dict after applying the cascade."""
    out = _env_layer()
    for layer in (collection, agent, request):
        out.update(_clean(layer))
    return out
