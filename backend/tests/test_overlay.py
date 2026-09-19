"""Desktop overlay — continuous chat session (Phase 20b, SPEC §21.5)."""

from __future__ import annotations

import pytest


async def _agents(auth_client) -> list[dict]:
    return (await auth_client.get("/api/agents")).json()


async def _overlay(auth_client, agent_id: str | None = None):
    body = {"agent_id": agent_id} if agent_id else {}
    return await auth_client.post("/api/overlay/session", json=body)


@pytest.mark.asyncio
async def test_overlay_session_is_idempotent(auth_client):
    agent_id = (await _agents(auth_client))[0]["id"]
    first = (await _overlay(auth_client, agent_id)).json()
    second = (await _overlay(auth_client, agent_id)).json()
    assert first["created"] is True
    assert second["created"] is False
    assert first["session"]["id"] == second["session"]["id"]
    assert first["session"]["origin"] == "overlay"


@pytest.mark.asyncio
async def test_overlay_session_defaults_to_the_default_agent(auth_client):
    agents = await _agents(auth_client)
    default = next(a for a in agents if a["is_default"])
    body = (await _overlay(auth_client)).json()
    assert body["session"]["agent_id"] == default["id"]


@pytest.mark.asyncio
async def test_each_agent_gets_its_own_continuous_session(auth_client):
    agents = await _agents(auth_client)
    a, b = agents[0]["id"], agents[1]["id"]
    sa = (await _overlay(auth_client, a)).json()["session"]["id"]
    sb = (await _overlay(auth_client, b)).json()["session"]["id"]
    assert sa != sb
    # going back to the first agent resumes its session — no duplicate
    again = (await _overlay(auth_client, a)).json()
    assert again["created"] is False and again["session"]["id"] == sa


@pytest.mark.asyncio
async def test_archiving_the_session_starts_a_new_one(auth_client):
    agent_id = (await _agents(auth_client))[0]["id"]
    old = (await _overlay(auth_client, agent_id)).json()["session"]["id"]
    r = await auth_client.patch(f"/api/conversations/{old}", json={"archived": True})
    assert r.status_code == 200
    fresh = (await _overlay(auth_client, agent_id)).json()
    assert fresh["created"] is True
    assert fresh["session"]["id"] != old
    # the archived one is still readable (history lives on in the web UI)
    assert (await auth_client.get(f"/api/conversations/{old}")).status_code == 200


@pytest.mark.asyncio
async def test_overlay_session_unknown_agent_404(auth_client):
    r = await _overlay(auth_client, "00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_overlay_session_requires_login(client):
    r = await client.post("/api/overlay/session", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_conversation_list_origin_filter_and_field(auth_client):
    agent_id = (await _agents(auth_client))[0]["id"]
    web = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()
    overlay = (await _overlay(auth_client, agent_id)).json()["session"]
    assert web["origin"] == "web"

    everything = (await auth_client.get("/api/conversations")).json()
    by_id = {c["id"]: c for c in everything}
    assert by_id[web["id"]]["origin"] == "web"
    assert by_id[overlay["id"]]["origin"] == "overlay"

    only_overlay = (await auth_client.get("/api/conversations?origin=overlay")).json()
    assert [c["id"] for c in only_overlay] == [overlay["id"]]
    only_web = (await auth_client.get("/api/conversations?origin=web")).json()
    assert web["id"] in [c["id"] for c in only_web]
    assert overlay["id"] not in [c["id"] for c in only_web]

    assert (await auth_client.get("/api/conversations?origin=bogus")).status_code == 400
