"""2D avatar config schema + on-disk model file resolution (SPEC §20).

``AvatarConfig`` validates the shape stored in ``agents.avatar_config``
(§20.2). ``resolve`` applies the same path-traversal discipline as
``sandbox/fs_ops.py::resolve`` — normalize, resolve symlinks, then verify
containment — used by both the static-file route (§20.4) and ``is_ready``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..config import get_settings

AI_STATES = ("idle", "thinking", "talking", "acting", "waiting")

# Folder-upload endpoint (§ below): only these file types are ever written to
# disk from an upload — a Cubism model never needs anything else, and this
# forecloses using the endpoint to drop arbitrary content on the server.
_ALLOWED_UPLOAD_SUFFIXES = (
    ".model3.json",
    ".moc3",
    ".physics3.json",
    ".pose3.json",
    ".cdi3.json",
    ".userdata3.json",
    ".motion3.json",
    ".exp3.json",
    ".png",
    ".wav",  # voice clips a motion can reference (Motions[].Sound); not used
    # for anything yet (no TTS/audio playback — SPEC §20.11 is not implemented),
    # but it's a legitimate, harmless static asset some official sample
    # models (e.g. Haru) ship with.
)


class InvalidAvatarPath(ValueError):
    pass


class AvatarUploadError(ValueError):
    pass


class AvatarConfig(BaseModel):
    model_config = {"extra": "forbid"}

    version: Literal[1] = 1
    enabled: bool = False
    model_path: str | None = None
    scale: float = Field(default=1.0, ge=0.01, le=10)
    offset_x: float = 0.0
    offset_y: float = 0.0
    idle_motion_group: str = "Idle"
    motion_map: dict[str, str] = Field(default_factory=dict)
    expression_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "AvatarConfig":
        if self.enabled and not (self.model_path or "").strip():
            raise ValueError("model_path is required when enabled=true")
        if self.model_path:
            rel = self.model_path.replace("\\", "/")
            if rel.startswith("/") or ".." in rel.split("/") or (len(rel) > 1 and rel[1] == ":"):
                raise ValueError("model_path must be a relative path with no '..' components")
            if not rel.endswith(".model3.json"):
                raise ValueError("model_path must point to a *.model3.json file")
        for mapping_name in ("motion_map", "expression_map"):
            bad = set(getattr(self, mapping_name)) - set(AI_STATES)
            if bad:
                raise ValueError(f"{mapping_name} has unknown state key(s): {sorted(bad)}")
        return self


# A model_path is looked up under the agent's own directory first, then
# falls back to this shared library — one upload, usable by every agent
# without copying it into each agent's own folder. "_shared" can never
# collide with a real agent id (those are UUIDs).
SHARED_LIBRARY_ID = "_shared"


def root_dir(agent_id: str) -> Path:
    return Path(get_settings().avatar_dir).expanduser() / agent_id


def shared_root_dir() -> Path:
    return Path(get_settings().avatar_dir).expanduser() / SHARED_LIBRARY_ID


def _resolve_under(root: Path, rel_path: str) -> Path:
    root = root.resolve()
    rel = (rel_path or "").strip()
    if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        raise InvalidAvatarPath("absolute paths are not allowed")
    target = (root / os.path.normpath(rel)).resolve()
    if target != root and root not in target.parents:
        raise InvalidAvatarPath("path escapes the allowed directory")
    return target


def resolve(agent_id: str, rel_path: str) -> Path:
    """Resolve ``rel_path`` under this agent's own avatar directory; if
    nothing exists there, fall back to the shared library
    (``data/avatars/_shared/``). Raises ``InvalidAvatarPath`` on any attempt
    to escape *either* root (including via symlink) — a traversal attempt is
    rejected outright rather than silently falling through to the other root.
    """
    own = _resolve_under(root_dir(agent_id), rel_path)
    if own.is_file():
        return own
    return _resolve_under(shared_root_dir(), rel_path)


def _models_under(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*.model3.json") if p.is_file()}


def list_models(agent_id: str) -> list[str]:
    """Every ``*.model3.json`` this agent can use: its own directory merged
    with the shared library, as paths relative to their respective root
    (posix separators, sorted, deduplicated by path string) — powers the
    Settings UI's model picker (no manual path typing). Empty, not an error,
    when neither directory has anything yet."""
    return sorted(_models_under(root_dir(agent_id)) | _models_under(shared_root_dir()))


def list_shared_models() -> list[str]:
    """Shared-library models only — usable before any agent exists (the
    picker for a not-yet-saved agent has no per-agent directory to look
    under yet, but the shared library isn't agent-scoped)."""
    return sorted(_models_under(shared_root_dir()))


def _safe_upload_rel_path(rel: str) -> str:
    """Normalize+validate one path as reported by the browser's folder
    picker (`File.webkitRelativePath`). Runs before anything touches disk —
    separate from ``resolve()``, which checks an already-existing path on
    disk against a live root."""
    rel = (rel or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise AvatarUploadError(f"invalid upload path: {rel!r}")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise AvatarUploadError(f"invalid upload path: {rel!r}")
    return "/".join(parts)


def _save_uploaded_model(final_root: Path, entries: list[tuple[str, bytes]]) -> str:
    """Stage + commit a browser folder-upload (``<input webkitdirectory>``,
    SPEC §20.5) into ``final_root``.

    ``entries`` is (relative_path, content) pairs exactly as the browser
    reported them. Every path is expected to share one top-level folder name
    — the folder the user picked — and that is the *only* subfolder touched:
    it fully replaces ``final_root/<top level>/`` (so re-uploading the same
    model overwrites cleanly) but leaves every sibling model folder already
    under ``final_root`` untouched. Returns the ``model_path`` (relative to
    ``final_root``) of the single ``*.model3.json`` found among them.

    Staged into a temp directory first and moved into place only once every
    file has validated and been written — a failure partway through never
    leaves the live directory half-written.
    """
    settings = get_settings()
    if not entries:
        raise AvatarUploadError("no files uploaded")
    if len(entries) > settings.max_avatar_files:
        raise AvatarUploadError(f"too many files (max {settings.max_avatar_files})")

    total_bytes = sum(len(data) for _, data in entries)
    if total_bytes > settings.max_avatar_mb * 1024 * 1024:
        raise AvatarUploadError(f"upload exceeds the {settings.max_avatar_mb} MB limit")

    normalized: list[tuple[str, bytes]] = []
    top_levels: set[str] = set()
    model3_paths: list[str] = []
    for rel, data in entries:
        safe = _safe_upload_rel_path(rel)
        if not safe.lower().endswith(_ALLOWED_UPLOAD_SUFFIXES):
            raise AvatarUploadError(f"file type not allowed: {safe}")
        top_levels.add(safe.split("/", 1)[0])
        if safe.lower().endswith(".model3.json"):
            model3_paths.append(safe)
        normalized.append((safe, data))

    if len(top_levels) != 1:
        raise AvatarUploadError(
            "select a single model folder — every file must share one top-level directory"
        )
    if len(model3_paths) != 1:
        raise AvatarUploadError(
            f"expected exactly one *.model3.json file in the folder, found {len(model3_paths)}"
        )

    top_level = next(iter(top_levels))

    staging = Path(tempfile.mkdtemp(prefix="pagi-avatar-upload-"))
    try:
        for rel, data in normalized:
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        final_root.mkdir(parents=True, exist_ok=True)
        dest = final_root / top_level
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(staging / top_level), str(dest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return model3_paths[0]


def save_uploaded_model(agent_id: str, entries: list[tuple[str, bytes]]) -> str:
    """Upload into this one agent's own directory — not visible to other
    agents' pickers. Prefer ``save_uploaded_model_shared`` unless the model
    is genuinely specific to this agent."""
    return _save_uploaded_model(root_dir(agent_id), entries)


def save_uploaded_model_shared(entries: list[tuple[str, bytes]]) -> str:
    """Upload into the shared library — usable by every agent's picker with
    no per-agent setup, and by an agent that hasn't been saved yet (no id,
    so no per-agent directory exists to upload into anyway)."""
    return _save_uploaded_model(shared_root_dir(), entries)


def is_ready_for(agent_id: str, avatar_config: dict) -> bool:
    """§20.5 ``avatar_ready`` — enabled AND model_path resolves to a real file
    on disk right now. Recomputed per request, never cached."""
    cfg = avatar_config or {}
    if not cfg.get("enabled") or not cfg.get("model_path"):
        return False
    try:
        target = resolve(agent_id, cfg["model_path"])
    except InvalidAvatarPath:
        return False
    return target.is_file()
