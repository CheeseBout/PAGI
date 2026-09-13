"""Fuse dense + sparse result lists (SPEC §14.2 group 2, §14.4 step 4).

``rrf``      — Reciprocal Rank Fusion: score = Σ 1/(k + rank). Rank-based, so it
               doesn't care that cosine and BM25 live on different scales.
``weighted`` — min-max normalise each list to [0,1], then
               ``alpha*dense + (1-alpha)*sparse``.
"""

from __future__ import annotations


def _rank_map(hits: list[tuple[str, float]]) -> dict[str, int]:
    return {cid: r for r, (cid, _) in enumerate(hits)}


def reciprocal_rank_fusion(
    dense: list[tuple[str, float]],
    sparse: list[tuple[str, float]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    dr, sr = _rank_map(dense), _rank_map(sparse)
    ids = set(dr) | set(sr)
    fused: list[tuple[str, float]] = []
    for cid in ids:
        s = 0.0
        if cid in dr:
            s += 1.0 / (k + dr[cid] + 1)
        if cid in sr:
            s += 1.0 / (k + sr[cid] + 1)
        fused.append((cid, s))
    fused.sort(key=lambda t: t[1], reverse=True)
    return fused


def _minmax(hits: list[tuple[str, float]]) -> dict[str, float]:
    if not hits:
        return {}
    vals = [s for _, s in hits]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    return {cid: (s - lo) / span for cid, s in hits}


def weighted_fusion(
    dense: list[tuple[str, float]],
    sparse: list[tuple[str, float]],
    *,
    alpha: float = 0.5,
) -> list[tuple[str, float]]:
    dn, sn = _minmax(dense), _minmax(sparse)
    ids = set(dn) | set(sn)
    fused = [
        (cid, alpha * dn.get(cid, 0.0) + (1 - alpha) * sn.get(cid, 0.0)) for cid in ids
    ]
    fused.sort(key=lambda t: t[1], reverse=True)
    return fused


def fuse(
    dense: list[tuple[str, float]],
    sparse: list[tuple[str, float]],
    cfg: dict,
) -> list[tuple[str, float]]:
    if not sparse:
        return dense
    if not dense:
        return sparse
    if (cfg.get("fusion") or "rrf") == "weighted":
        return weighted_fusion(dense, sparse, alpha=float(cfg.get("hybrid_alpha") or 0.5))
    return reciprocal_rank_fusion(dense, sparse, k=int(cfg.get("rrf_k") or 60))
