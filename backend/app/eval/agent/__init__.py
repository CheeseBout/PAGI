"""Agent / trajectory evaluation (Phase 16, PLAN §16).

Scores the *path*, not only the final answer: step efficiency, redundant tool
calls, parameter hallucination, forbidden-tool use, task resolution — plus the
cost / latency / LLM-call counts needed to compare patterns honestly.
"""

from __future__ import annotations

from .metrics import trajectory_metrics
from .runner import run, schedule
from .synth import synthesize

__all__ = ["trajectory_metrics", "run", "schedule", "synthesize"]
