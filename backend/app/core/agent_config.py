"""Apply a field patch onto a live ``Agent`` row.

Factored out of ``routes_agents.py::update_agent`` so there is exactly one
piece of code that mutates ``agents.*`` — both the normal PATCH endpoint and
the Phase 17 harness (regression-gate activation, rollback) call this instead
of each re-implementing the setattr loop. PLAN §2 principle 13 / §8 checklist:
no code path may write ``agents.system_prompt``/``tools_allowed``/etc. other
than ones that go through here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db.models import Agent


def apply_patch(agent: Agent, data: dict) -> None:
    for key, value in data.items():
        setattr(agent, key, value)
    agent.updated_at = datetime.now(timezone.utc)
