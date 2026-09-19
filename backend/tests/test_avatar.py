"""2D avatar: config validation, avatar_ready, static file route (SPEC §20)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clean_shared_avatar_library():
    """The shared library (unlike per-agent dirs, which are namespaced by a
    fresh UUID every test via `_reset_db`) isn't scoped to anything the DB
    reset touches — wipe it before each test so upload/list assertions here
    don't see another test's uploads."""
    shutil.rmtree(Path(get_settings().avatar_dir) / "_shared", ignore_errors=True)
    yield


async def _agent_id(auth_client) -> str:
    agents = (await auth_client.get("/api/agents")).json()
    return agents[0]["id"]


@pytest.mark.asyncio
async def test_avatar_config_defaults_to_empty_and_not_ready(auth_client):
    agent_id = await _agent_id(auth_client)
    r = await auth_client.get(f"/api/agents/{agent_id}")
    body = r.json()
    assert body["avatar_config"] == {}
    assert body["avatar_ready"] is False


@pytest.mark.asyncio
async def test_avatar_config_rejects_bad_scale(auth_client):
    agent_id = await _agent_id(auth_client)
    r = await auth_client.patch(
        f"/api/agents/{agent_id}",
        json={"avatar_config": {"enabled": True, "model_path": "x/x.model3.json", "scale": 99}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_avatar_config_rejects_path_traversal(auth_client):
    agent_id = await _agent_id(auth_client)
    r = await auth_client.patch(
        f"/api/agents/{agent_id}",
        json={"avatar_config": {"enabled": True, "model_path": "../../etc/x.model3.json"}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_avatar_config_requires_model_path_when_enabled(auth_client):
    agent_id = await _agent_id(auth_client)
    r = await auth_client.patch(f"/api/agents/{agent_id}", json={"avatar_config": {"enabled": True}})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_avatar_ready_true_once_file_exists_on_disk(auth_client):
    agent_id = await _agent_id(auth_client)
    rel = "hiyori/hiyori.model3.json"
    r = await auth_client.patch(
        f"/api/agents/{agent_id}",
        json={"avatar_config": {"enabled": True, "model_path": rel}},
    )
    assert r.status_code == 200
    assert r.json()["avatar_ready"] is False  # file not on disk yet

    model_file = Path(get_settings().avatar_dir) / agent_id / rel
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("{}", encoding="utf-8")

    r = await auth_client.get(f"/api/agents/{agent_id}")
    assert r.json()["avatar_ready"] is True


@pytest.mark.asyncio
async def test_avatar_static_route_serves_file_with_nosniff(auth_client):
    agent_id = await _agent_id(auth_client)
    model_file = Path(get_settings().avatar_dir) / agent_id / "m.model3.json"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text('{"ok": true}', encoding="utf-8")

    r = await auth_client.get(f"/avatars/{agent_id}/m.model3.json")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_avatar_static_route_404_unknown_agent(auth_client):
    r = await auth_client.get("/avatars/does-not-exist/m.model3.json")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_avatar_static_route_400_path_traversal(auth_client):
    agent_id = await _agent_id(auth_client)
    r = await auth_client.get(f"/avatars/{agent_id}/../../etc/passwd")
    # the path component is normalized by the ASGI router itself for a plain
    # "..", so also exercise an encoded traversal that reaches our own resolve()
    assert r.status_code in (400, 404)

    r2 = await auth_client.get(f"/avatars/{agent_id}/..%2f..%2fetc%2fpasswd")
    assert r2.status_code in (400, 404)


@pytest.mark.asyncio
async def test_avatar_models_empty_when_no_directory(auth_client):
    agent_id = await _agent_id(auth_client)
    r = await auth_client.get(f"/api/agents/{agent_id}/avatar-models")
    assert r.status_code == 200
    assert r.json() == {"models": []}


@pytest.mark.asyncio
async def test_avatar_models_lists_nested_model3_json_sorted(auth_client):
    agent_id = await _agent_id(auth_client)
    root = Path(get_settings().avatar_dir) / agent_id
    (root / "hiyori").mkdir(parents=True, exist_ok=True)
    (root / "hiyori" / "Hiyori.model3.json").write_text("{}", encoding="utf-8")
    (root / "mao").mkdir(parents=True, exist_ok=True)
    (root / "mao" / "Mao.model3.json").write_text("{}", encoding="utf-8")
    # a stray non-model file must not show up
    (root / "hiyori" / "Hiyori.physics3.json").write_text("{}", encoding="utf-8")

    r = await auth_client.get(f"/api/agents/{agent_id}/avatar-models")
    assert r.status_code == 200
    assert r.json() == {"models": ["hiyori/Hiyori.model3.json", "mao/Mao.model3.json"]}


@pytest.mark.asyncio
async def test_avatar_models_404_unknown_agent(auth_client):
    r = await auth_client.get("/api/agents/does-not-exist/avatar-models")
    assert r.status_code == 404


def _folder_files(pairs: list[tuple[str, bytes]]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Build an httpx `files=` list matching what `<input webkitdirectory>`
    produces client-side: one multipart field per file, named for its full
    relative path (not a fixed "files" key)."""
    return [(path, (path.rsplit("/", 1)[-1], data, "application/octet-stream")) for path, data in pairs]


@pytest.mark.asyncio
async def test_avatar_upload_folder_success(auth_client):
    agent_id = await _agent_id(auth_client)
    files = _folder_files([
        ("hiyori/Hiyori.model3.json", b'{"ok": true}'),
        ("hiyori/textures/texture_00.png", b"\x89PNG fake"),
    ])
    r = await auth_client.post(f"/api/agents/{agent_id}/avatar/upload", files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["model_path"] == "hiyori/Hiyori.model3.json"
    assert body["models"] == ["hiyori/Hiyori.model3.json"]

    on_disk = Path(get_settings().avatar_dir) / agent_id / "hiyori"
    assert (on_disk / "Hiyori.model3.json").read_bytes() == b'{"ok": true}'
    assert (on_disk / "textures" / "texture_00.png").is_file()


@pytest.mark.asyncio
async def test_avatar_upload_replaces_only_its_own_subfolder(auth_client):
    agent_id = await _agent_id(auth_client)
    await auth_client.post(
        f"/api/agents/{agent_id}/avatar/upload",
        files=_folder_files([("hiyori/Hiyori.model3.json", b"v1")]),
    )
    r = await auth_client.post(
        f"/api/agents/{agent_id}/avatar/upload",
        files=_folder_files([("mao/Mao.model3.json", b"v1")]),
    )
    assert r.status_code == 201
    assert sorted(r.json()["models"]) == ["hiyori/Hiyori.model3.json", "mao/Mao.model3.json"]

    # re-upload hiyori with new content — overwrites cleanly, doesn't touch mao
    r2 = await auth_client.post(
        f"/api/agents/{agent_id}/avatar/upload",
        files=_folder_files([("hiyori/Hiyori.model3.json", b"v2")]),
    )
    assert r2.status_code == 201
    on_disk = Path(get_settings().avatar_dir) / agent_id
    assert (on_disk / "hiyori" / "Hiyori.model3.json").read_bytes() == b"v2"
    assert (on_disk / "mao" / "Mao.model3.json").is_file()


@pytest.mark.asyncio
async def test_avatar_upload_rejects_disallowed_extension(auth_client):
    agent_id = await _agent_id(auth_client)
    files = _folder_files([
        ("hiyori/Hiyori.model3.json", b"{}"),
        ("hiyori/evil.exe", b"MZ"),
    ])
    r = await auth_client.post(f"/api/agents/{agent_id}/avatar/upload", files=files)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_avatar_upload_rejects_multiple_top_level_folders(auth_client):
    agent_id = await _agent_id(auth_client)
    files = _folder_files([
        ("hiyori/Hiyori.model3.json", b"{}"),
        ("other/Other.model3.json", b"{}"),
    ])
    r = await auth_client.post(f"/api/agents/{agent_id}/avatar/upload", files=files)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_avatar_upload_rejects_zero_or_multiple_model3json(auth_client):
    agent_id = await _agent_id(auth_client)
    r_zero = await auth_client.post(
        f"/api/agents/{agent_id}/avatar/upload",
        files=_folder_files([("hiyori/texture.png", b"x")]),
    )
    assert r_zero.status_code == 400

    r_multi = await auth_client.post(
        f"/api/agents/{agent_id}/avatar/upload",
        files=_folder_files([
            ("hiyori/A.model3.json", b"{}"),
            ("hiyori/sub/B.model3.json", b"{}"),
        ]),
    )
    assert r_multi.status_code == 400


@pytest.mark.asyncio
async def test_avatar_upload_rejects_path_traversal(auth_client):
    agent_id = await _agent_id(auth_client)
    files = _folder_files([("../evil/Hiyori.model3.json", b"{}")])
    r = await auth_client.post(f"/api/agents/{agent_id}/avatar/upload", files=files)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_avatar_upload_404_unknown_agent(auth_client):
    files = _folder_files([("hiyori/Hiyori.model3.json", b"{}")])
    r = await auth_client.post("/api/agents/does-not-exist/avatar/upload", files=files)
    assert r.status_code == 404


# ── shared library ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_avatar_library_empty_when_no_directory(auth_client):
    r = await auth_client.get("/api/avatar-library")
    assert r.status_code == 200
    assert r.json() == {"models": []}


@pytest.mark.asyncio
async def test_avatar_library_upload_and_list(auth_client):
    files = _folder_files([
        ("mao/Mao.model3.json", b'{"ok": true}'),
        ("mao/textures/texture_00.png", b"\x89PNG"),
    ])
    r = await auth_client.post("/api/avatar-library/upload", files=files)
    assert r.status_code == 201, r.text
    assert r.json() == {"model_path": "mao/Mao.model3.json", "models": ["mao/Mao.model3.json"]}

    r2 = await auth_client.get("/api/avatar-library")
    assert r2.json() == {"models": ["mao/Mao.model3.json"]}

    on_disk = Path(get_settings().avatar_dir) / "_shared" / "mao"
    assert (on_disk / "Mao.model3.json").read_bytes() == b'{"ok": true}'


@pytest.mark.asyncio
async def test_avatar_library_upload_validates_same_as_per_agent(auth_client):
    r = await auth_client.post(
        "/api/avatar-library/upload",
        files=_folder_files([("mao/evil.exe", b"MZ")]),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_avatar_models_falls_back_to_shared_library(auth_client):
    agent_id = await _agent_id(auth_client)
    await auth_client.post(
        "/api/avatar-library/upload",
        files=_folder_files([("wanko/Wanko.model3.json", b"{}")]),
    )
    # the agent has nothing of its own — the shared model still shows up in
    # its picker, and resolves/serves without ever being copied per-agent.
    r = await auth_client.get(f"/api/agents/{agent_id}/avatar-models")
    assert r.json() == {"models": ["wanko/Wanko.model3.json"]}

    r2 = await auth_client.patch(
        f"/api/agents/{agent_id}",
        json={"avatar_config": {"enabled": True, "model_path": "wanko/Wanko.model3.json"}},
    )
    assert r2.json()["avatar_ready"] is True

    r3 = await auth_client.get(f"/avatars/{agent_id}/wanko/Wanko.model3.json")
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_avatar_own_copy_takes_precedence_over_shared(auth_client):
    agent_id = await _agent_id(auth_client)
    await auth_client.post(
        "/api/avatar-library/upload",
        files=_folder_files([("ren/Ren.model3.json", b"shared-version")]),
    )
    await auth_client.post(
        f"/api/agents/{agent_id}/avatar/upload",
        files=_folder_files([("ren/Ren.model3.json", b"own-version")]),
    )
    r = await auth_client.get(f"/avatars/{agent_id}/ren/Ren.model3.json")
    assert r.text == "own-version"


@pytest.mark.asyncio
async def test_avatar_library_upload_rejects_path_traversal(auth_client):
    files = _folder_files([("../evil/Hiyori.model3.json", b"{}")])
    r = await auth_client.post("/api/avatar-library/upload", files=files)
    assert r.status_code == 400
