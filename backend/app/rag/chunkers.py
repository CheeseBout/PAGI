"""Text -> chunks (SPEC §14.3, §14.2 group 1).

Strategies:
  recursive     — split on the biggest separator that keeps chunks under budget
  fixed         — hard character windows
  parent_child  — recursive *parents* (large), each split into recursive
                  *children* (small); only children are embedded, the parent is
                  swapped back in at retrieval time (SPEC §14.4 step 6)
  semantic      — sentence split, embed sentences, break where the cosine
                  distance between neighbours exceeds a percentile

``chunk_text`` (sync) is the recursive core, kept for tests. ``build_chunks``
(async) is what ingest calls — it dispatches by strategy and can embed for the
semantic path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger("pagi.rag.chunk")

_CHARS_PER_TOKEN = 4
_SEPARATORS = ["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""]
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[^\s])|\n{2,}")


@dataclass
class Chunk:
    text: str
    ordinal: int
    token_count: int
    meta: dict = field(default_factory=dict)
    parent_ordinal: int | None = None
    #: what actually gets embedded — enrichment (12d) sets this; "" = use ``text``
    text_embedded: str = ""

    def embed_text(self) -> str:
        return self.text_embedded or self.text


@dataclass
class ChunkSet:
    chunks: list[Chunk]              # the indexed units
    parents: list[Chunk] = field(default_factory=list)  # parent_child only


def _toklen(s: str) -> int:
    return max(1, len(s) // _CHARS_PER_TOKEN)


def _split_recursive(text: str, max_chars: int, seps: list[str]) -> list[str]:
    if len(text) <= max_chars or not seps:
        return [text] if text.strip() else []
    sep, rest = seps[0], seps[1:]
    parts = text.split(sep) if sep else list(text)
    out: list[str] = []
    buf = ""
    for part in parts:
        piece = (part + sep) if sep else part
        if len(buf) + len(piece) <= max_chars:
            buf += piece
            continue
        if buf.strip():
            out.append(buf)
        if len(piece) > max_chars:
            out.extend(_split_recursive(piece, max_chars, rest))
            buf = ""
        else:
            buf = piece
    if buf.strip():
        out.append(buf)
    return out


def _merge_small(pieces: list[str], target_chars: int) -> list[str]:
    merged: list[str] = []
    buf = ""
    for p in pieces:
        if not buf:
            buf = p
        elif len(buf) + len(p) <= target_chars:
            buf += p
        else:
            merged.append(buf)
            buf = p
    if buf.strip():
        merged.append(buf)
    return merged


def _recursive_pieces(norm: str, max_chars: int, overlap_chars: int) -> list[str]:
    raw = _merge_small(_split_recursive(norm, max_chars, _SEPARATORS), max_chars)
    out: list[str] = []
    prev_tail = ""
    for body in raw:
        body = body.strip()
        if not body:
            continue
        out.append((prev_tail + body) if (prev_tail and overlap_chars) else body)
        prev_tail = body[-overlap_chars:] if overlap_chars else ""
    return out


def chunk_text(text: str, cfg: dict) -> list[Chunk]:
    """Recursive / fixed only — the sync core (used by tests and by build_chunks)."""
    strategy = cfg.get("chunk_strategy") or "recursive"
    size_tok = int(cfg.get("chunk_size") or 400)
    overlap_pct = float(cfg.get("chunk_overlap_pct") or 0.12)
    max_chars = size_tok * _CHARS_PER_TOKEN
    overlap_chars = int(max_chars * max(0.0, min(overlap_pct, 0.5)))

    norm = re.sub(r"\r\n?", "\n", text).strip()
    if not norm:
        return []

    if strategy == "fixed":
        step = max(1, max_chars - overlap_chars)
        raw = [norm[i : i + max_chars] for i in range(0, len(norm), step)]
    else:
        raw = _recursive_pieces(norm, max_chars, overlap_chars)

    return [
        Chunk(text=b.strip(), ordinal=i, token_count=_toklen(b), meta={})
        for i, b in enumerate(b for b in raw if b.strip())
    ]


def _parent_child(text: str, cfg: dict) -> ChunkSet:
    child_tok = int(cfg.get("chunk_size") or 400)
    parent_tok = int(cfg.get("parent_chunk_size") or 2000)
    overlap_pct = float(cfg.get("chunk_overlap_pct") or 0.12)
    norm = re.sub(r"\r\n?", "\n", text).strip()
    if not norm:
        return ChunkSet(chunks=[])

    parent_pieces = _recursive_pieces(norm, parent_tok * _CHARS_PER_TOKEN, 0)
    parents: list[Chunk] = []
    children: list[Chunk] = []
    cmax = child_tok * _CHARS_PER_TOKEN
    coverlap = int(cmax * max(0.0, min(overlap_pct, 0.5)))
    for pi, ptext in enumerate(parent_pieces):
        ptext = ptext.strip()
        if not ptext:
            continue
        parents.append(Chunk(text=ptext, ordinal=pi, token_count=_toklen(ptext), meta={}))
        for cpiece in _recursive_pieces(ptext, cmax, coverlap):
            cpiece = cpiece.strip()
            if not cpiece:
                continue
            children.append(
                Chunk(
                    text=cpiece,
                    ordinal=len(children),
                    token_count=_toklen(cpiece),
                    parent_ordinal=pi,
                    meta={},
                )
            )
    return ChunkSet(chunks=children, parents=parents)


async def _semantic(text: str, cfg: dict, *, embed_model: str) -> ChunkSet:
    from .embeddings import embed_texts

    norm = re.sub(r"\r\n?", "\n", text).strip()
    sents = [s.strip() for s in _SENT_RE.split(norm) if s.strip()]
    if len(sents) < 4:
        return ChunkSet(chunks=chunk_text(text, {**cfg, "chunk_strategy": "recursive"}))

    pct = int(cfg.get("semantic_breakpoint_percentile") or 95)
    max_chars = int(cfg.get("chunk_size") or 400) * _CHARS_PER_TOKEN * 2

    try:
        vecs = await embed_texts(embed_model, sents)
    except Exception as exc:  # pragma: no cover - external
        log.warning("semantic_embed_failed", error=str(exc))
        return ChunkSet(chunks=chunk_text(text, {**cfg, "chunk_strategy": "recursive"}))

    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    dists = [1.0 - _cos(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
    if dists:
        ordered = sorted(dists)
        cut = ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]
    else:
        cut = 1.0

    chunks: list[Chunk] = []
    buf = sents[0]
    for i in range(1, len(sents)):
        if (dists[i - 1] >= cut and len(buf) > 200) or len(buf) + len(sents[i]) > max_chars:
            chunks.append(Chunk(text=buf, ordinal=len(chunks), token_count=_toklen(buf)))
            buf = sents[i]
        else:
            buf += " " + sents[i]
    if buf.strip():
        chunks.append(Chunk(text=buf, ordinal=len(chunks), token_count=_toklen(buf)))
    return ChunkSet(chunks=chunks)


async def build_chunks(text: str, cfg: dict, *, embed_model: str) -> ChunkSet:
    strategy = cfg.get("chunk_strategy") or "recursive"
    if strategy == "parent_child":
        return _parent_child(text, cfg)
    if strategy == "semantic":
        return await _semantic(text, cfg, embed_model=embed_model)
    return ChunkSet(chunks=chunk_text(text, cfg))
