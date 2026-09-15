"""Single entry point for the full mine -> propose -> validate pipeline.

Fire-and-forget, mirroring ``eval/agent/runner.py::schedule()``. Any stage
returning ``None`` (nothing to mine, locked, judge failure, bad proposal)
quietly stops the pipeline for this run — that's the normal "no action
needed" outcome, not an error.
"""

from __future__ import annotations

import asyncio

import structlog

from .miner import mine
from .proposal import propose
from .regression import validate

log = structlog.get_logger("pagi.harness.service")


async def run_cycle(run_id: str) -> None:
    try:
        report = await mine(run_id)
        if report is None:
            return
        version = await propose(report.id)
        if version is None:
            return
        await validate(version.id)
    except Exception as exc:  # pragma: no cover - never break the caller
        log.warning("harness_run_cycle_failed", run_id=run_id, error=str(exc))


def schedule(run_id: str) -> None:
    asyncio.create_task(run_cycle(run_id))
