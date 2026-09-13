"""Agent-level vision_enabled toggle (gates image parts before the LLM call)."""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.core import attachments as att
from app.db.models import Agent, ChatSession, Message, User
from app.db.session import SessionLocal
from app.providers import DoneEvent, TextDelta

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_CAPTURED: dict = {}


class _CapturingProvider:
    def __init__(self, model: str):
        self.model = model

    async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
        _CAPTURED["messages"] = messages
        yield TextDelta("ok")
        yield DoneEvent(finish_reason="stop")


@pytest.fixture
def capturing(monkeypatch):
    monkeypatch.setattr(
        "app.core.agent_runtime.get_provider", lambda name: _CapturingProvider(name)
    )
    monkeypatch.setattr(
        "app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True
    )


async def _mk_session_with_image(vision_enabled: bool, tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(att, "_root", lambda: tmp_path)

    async with SessionLocal() as db:
        uid = (await db.exec(select(User))).first().id
        agent = Agent(
            name="Vis", provider="openai", model="vision-model",
            vision_enabled=vision_enabled,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        s = ChatSession(user_id=uid, agent_id=agent.id)
        db.add(s)
        await db.commit()
        await db.refresh(s)

        meta = att.save(s.id, filename="a.png", content_type="image/png", data=_PNG)
        db.add(Message(session_id=s.id, role="user", content="what is this", attachments=[meta]))
        await db.commit()
        return s.id


@pytest.mark.asyncio
async def test_vision_enabled_agent_sees_image(auth_client, capturing, tmp_path, monkeypatch):
    from app.core.agent_runtime import run_turn

    sid = await _mk_session_with_image(True, tmp_path, monkeypatch)
    await run_turn(sid, wait_for_approval=True)

    user_msg = next(m for m in _CAPTURED["messages"] if m.get("role") == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


@pytest.mark.asyncio
async def test_vision_disabled_agent_strips_image(auth_client, capturing, tmp_path, monkeypatch):
    from app.core.agent_runtime import run_turn

    sid = await _mk_session_with_image(False, tmp_path, monkeypatch)
    await run_turn(sid, wait_for_approval=True)

    user_msg = next(m for m in _CAPTURED["messages"] if m.get("role") == "user")
    assert user_msg["content"] == "what is this"
