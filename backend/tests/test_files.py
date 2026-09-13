"""Chat file upload / retrieval (SPEC §2.4)."""

import pytest

# 1x1 transparent PNG
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _new_conversation(client) -> str:
    agents = (await client.get("/api/agents")).json()
    default = next(a for a in agents if a["is_default"])
    r = await client.post("/api/conversations", json={"agent_id": default["id"]})
    return r.json()["id"]


@pytest.mark.asyncio
async def test_upload_get_delete_image(auth_client):
    sid = await _new_conversation(auth_client)

    r = await auth_client.post(
        f"/api/conversations/{sid}/files",
        files={"file": ("dot.png", _PNG, "image/png")},
    )
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["kind"] == "image"
    assert meta["size_bytes"] == len(_PNG)
    aid = meta["id"]

    r = await auth_client.get(f"/api/conversations/{sid}/files/{aid}")
    assert r.status_code == 200
    assert r.content == _PNG
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"

    r = await auth_client.delete(f"/api/conversations/{sid}/files/{aid}")
    assert r.status_code == 204
    r = await auth_client.get(f"/api/conversations/{sid}/files/{aid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reject_unsupported_type(auth_client):
    sid = await _new_conversation(auth_client)
    r = await auth_client.post(
        f"/api/conversations/{sid}/files",
        files={"file": ("x.bin", b"\x00\x01", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_type"


@pytest.mark.asyncio
async def test_reject_too_large(auth_client, monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    try:
        sid = await _new_conversation(auth_client)
        r = await auth_client.post(
            f"/api/conversations/{sid}/files",
            files={"file": ("dot.png", _PNG, "image/png")},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "too_large"
    finally:
        monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_upload_requires_ownership(auth_client):
    r = await auth_client.post(
        "/api/conversations/not-a-real-session/files",
        files={"file": ("dot.png", _PNG, "image/png")},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_message_carries_attachment(auth_client):
    sid = await _new_conversation(auth_client)
    r = await auth_client.post(
        f"/api/conversations/{sid}/files",
        files={"file": ("note.txt", b"hello world", "text/plain")},
    )
    aid = r.json()["id"]

    # non-streaming fallback runs a turn; the provider call will fail (no key),
    # but the user message + attachment must be persisted first.
    await auth_client.post(
        f"/api/conversations/{sid}/messages",
        json={"content": "look at this", "attachment_ids": [aid]},
    )
    convo = (await auth_client.get(f"/api/conversations/{sid}")).json()
    user_msg = next(m for m in convo["messages"] if m["role"] == "user")
    assert user_msg["attachments"][0]["id"] == aid
    assert user_msg["attachments"][0]["filename"] == "note.txt"
