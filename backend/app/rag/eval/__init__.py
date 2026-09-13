"""RAG evaluation (SPEC §14.8, PLAN §12f).

    ragas.py   — the four LLM-as-judge metrics
    synth.py   — Evol-Instruct golden-set generation + a Critic filter
    runner.py  — run a golden set against a RagConfig, store per-case scores
"""

from __future__ import annotations
