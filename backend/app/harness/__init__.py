"""Self-improving harness (Phase 17, PLAN §17).

Weakness Miner -> Harness Proposal -> Proposal Validation (regression gate).
Every step writes a candidate row; only the regression gate (or an explicit
rollback) may ever write ``agents.*`` (PLAN §2 principle 13).
"""

from __future__ import annotations

from .miner import failing_case_ids, mine
from .proposal import propose
from .regression import validate
from .rollback import RollbackError, rollback
from .service import run_cycle, schedule

__all__ = [
    "mine",
    "failing_case_ids",
    "propose",
    "validate",
    "rollback",
    "RollbackError",
    "run_cycle",
    "schedule",
]
