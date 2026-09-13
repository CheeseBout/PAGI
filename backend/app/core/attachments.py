"""Chat attachment storage (SPEC §2.4).

Files live on the backend disk at ``UPLOAD_DIR/<session_id>/<id><ext>`` with a
``<id>.json`` sidecar holding the metadata. The metadata dict is also embedded
in ``messages.attachments`` when the file is sent with a message. Images are
inlined into the LLM request as ``image_url`` parts; small text files are inlined
as text; anything else is download-only (or pushed into the sandbox workspace).
"""

from __future__ import annotations

import base64
import functools
import io
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

from ..config import get_settings

_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
_ALLOWED_PREFIXES = ("image/", "text/")
_ALLOWED_EXACT = {"application/pdf", "application/json"}

# text-ish attachments up to this size get inlined into the prompt as text
_TEXT_INLINE_LIMIT = 20_000

# images larger than this (longest edge, px) are downscaled on upload so they
# don't blow up token counts every time the history is re-sent (Wave 3b).
_IMAGE_MAX_EDGE = 1568

_ID_RE = re.compile(r"[0-9a-fA-F-]{36}")


def _shrink_image(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Best-effort downscale/recompress. Returns (data, content_type) unchanged
    on any failure or when the image is already small."""
    try:
        from PIL import Image
    except Exception:
        return data, content_type
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return data, content_type
    if max(img.size) <= _IMAGE_MAX_EDGE and len(data) <= 1_500_000:
        return data, content_type
    img.thumbnail((_IMAGE_MAX_EDGE, _IMAGE_MAX_EDGE))
    buf = io.BytesIO()
    has_alpha = img.mode in ("RGBA", "LA", "P")
    try:
        if has_alpha:
            img.convert("RGBA").save(buf, format="PNG", optimize=True)
            out_ct = "image/png"
        else:
            img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
            out_ct = "image/jpeg"
    except Exception:
        return data, content_type
    shrunk = buf.getvalue()
    if shrunk and len(shrunk) < len(data):
        return shrunk, out_ct
    return data, content_type


def _root() -> Path:
    p = Path(get_settings().upload_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_dir(session_id: str) -> Path:
    d = _root() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_allowed(content_type: str) -> bool:
    ct = _norm_ct(content_type)
    return ct in _ALLOWED_EXACT or ct.startswith(_ALLOWED_PREFIXES)


def _norm_ct(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower() or "application/octet-stream"


def _kind(content_type: str) -> str:
    return "image" if _norm_ct(content_type) in _IMAGE_TYPES else "file"


def save(session_id: str, *, filename: str, content_type: str, data: bytes) -> dict:
    att_id = str(uuid4())
    norm_ct = _norm_ct(content_type)
    if _kind(norm_ct) == "image":
        data, norm_ct = _shrink_image(data, norm_ct)
    ext = "".join(c for c in Path(filename or "").suffix if c.isalnum() or c == ".")[:16]
    if norm_ct == "image/jpeg" and ext.lower() not in (".jpg", ".jpeg"):
        ext = ".jpg"
    elif norm_ct == "image/png" and ext.lower() != ".png":
        ext = ".png"
    stored_name = f"{att_id}{ext}"
    (_session_dir(session_id) / stored_name).write_bytes(data)
    meta = {
        "id": att_id,
        "kind": _kind(norm_ct),
        "filename": filename or stored_name,
        "content_type": norm_ct,
        "size_bytes": len(data),
        "stored_name": stored_name,
    }
    (_session_dir(session_id) / f"{att_id}.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    return meta


def get_meta(session_id: str, att_id: str) -> dict | None:
    if not _ID_RE.fullmatch(att_id or ""):
        return None
    try:
        return json.loads((_root() / session_id / f"{att_id}.json").read_text("utf-8"))
    except OSError:
        return None


def collect(session_id: str, ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for aid in ids or []:
        meta = get_meta(session_id, aid)
        if meta is not None:
            out.append(meta)
    return out


def load_bytes(session_id: str, meta: dict) -> bytes | None:
    name = meta.get("stored_name")
    if not name:
        return None
    try:
        return (_root() / session_id / name).read_bytes()
    except OSError:
        return None


@functools.lru_cache(maxsize=128)
def _data_url_cached(root: str, session_id: str, stored_name: str, content_type: str) -> str | None:
    try:
        raw = (Path(root) / session_id / stored_name).read_bytes()
    except OSError:
        return None
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def data_url(session_id: str, meta: dict) -> str | None:
    """base64 data URI for an attachment. Cached per (file, type) so the agent
    loop doesn't re-read + re-encode the same image on every iteration."""
    name = meta.get("stored_name")
    if not name:
        return None
    return _data_url_cached(
        str(_root()), session_id, name, meta.get("content_type", "application/octet-stream")
    )


def inline_text(session_id: str, meta: dict) -> str | None:
    """Decoded text for a small text/* or json attachment, else None."""
    ct = meta.get("content_type", "")
    if not (ct.startswith("text/") or ct == "application/json"):
        return None
    if (meta.get("size_bytes") or 0) > _TEXT_INLINE_LIMIT:
        return None
    raw = load_bytes(session_id, meta)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def delete_one(session_id: str, att_id: str) -> None:
    meta = get_meta(session_id, att_id)
    if meta is None:
        return
    for name in (meta.get("stored_name"), f"{att_id}.json"):
        if name:
            (_root() / session_id / name).unlink(missing_ok=True)


def delete_many(session_id: str, metas: list[dict]) -> None:
    for meta in metas or []:
        delete_one(session_id, meta.get("id", ""))


def delete_for_session(session_id: str) -> None:
    shutil.rmtree(_root() / session_id, ignore_errors=True)
