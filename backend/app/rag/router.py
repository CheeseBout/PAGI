"""Heuristic query-complexity router (SPEC §14.2, §14.5).

Cheap, LLM-free. Maps a query to ``simple`` / ``medium`` / ``complex`` and, for
``query_transform="auto"``, to a concrete transform:

    simple  -> none
    medium  -> step_back
    complex -> decompose
"""

from __future__ import annotations

import re

_MULTI_HOP = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference|differ|relationship|both|"
    r"between .+ and |trade[- ]?off|pros and cons|correlat)", re.IGNORECASE
)
_CONJ = re.compile(r"\b(and|or|as well as|along with)\b", re.IGNORECASE)
_SIMPLE_START = re.compile(r"^\s*(what|who|when|where|which|is|are|does|do|how many|how much)\b",
                           re.IGNORECASE)


def classify(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "simple"
    words = q.split()
    n_words = len(words)
    n_q = q.count("?")
    n_conj = len(_CONJ.findall(q))

    if _MULTI_HOP.search(q) or n_q >= 2 or n_conj >= 2 or n_words > 32:
        return "complex"
    if n_words <= 9 and n_conj == 0 and _SIMPLE_START.match(q):
        return "simple"
    return "medium"


def transform_for(query: str) -> str:
    return {"simple": "none", "medium": "step_back", "complex": "decompose"}[classify(query)]
