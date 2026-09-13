"""Placeholder for future external chat channels (Telegram/Slack/…).

Deliberately NOT built for the MVP (PLAN §1 "out of scope"). Kept as a seam:
a channel would translate inbound platform events into `run_turn` calls and
forward streamed events back out.
"""

from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
