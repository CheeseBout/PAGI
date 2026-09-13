"""``VectorStore`` protocol — the seam for swapping numpy brute-force for
pgvector / sqlite-vec later without touching the pipeline (SPEC §14.10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    score: float  # cosine similarity in [-1, 1]


class VectorStore(Protocol):
    async def query(
        self,
        collection_id: str,
        query_vec: list[float],
        top_k: int,
        *,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[VectorHit]:
        """Nearest chunks in a collection by cosine similarity.

        ``allowed_chunk_ids`` (when given) pre-filters the candidate set — the
        pipeline uses it to apply metadata filters computed from the ORM.
        """
        ...

    def invalidate(self, collection_id: str) -> None:
        """Drop any cached index for a collection (call after ingest / delete)."""
        ...
