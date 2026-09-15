"""Sandbox HTTP service (SPEC §6). Internal-only; never expose to the internet."""

from __future__ import annotations

import base64
import binascii
import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

import docker_runner
import fs_ops
import limits
import network_guard
from config import config

log = logging.getLogger("pagi.sandbox")
app = FastAPI(title="PAGI Sandbox", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    # Best-effort — never block boot on Docker/network setup issues.
    removed = docker_runner.reap_orphans()
    if removed:
        log.info("startup: reaped %d orphaned exec container(s)", removed)
    net_status = network_guard.setup()
    log.info("startup: egress network filter = %s", net_status)


async def require_token(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != config.internal_token:
        raise HTTPException(status_code=401, detail={"error": {"code": "unauthorized", "message": "bad internal token"}})


def _bad_path(exc: fs_ops.InvalidPath) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": {"code": "invalid_path", "message": str(exc)}})


def _quota_guard(session_id: str, incoming_bytes: int = 0) -> None:
    try:
        ws = fs_ops.workspace_dir(session_id)
        limits.check_quota(ws, incoming_bytes)
    except limits.QuotaExceeded as exc:
        raise HTTPException(
            status_code=413, detail={"error": {"code": "workspace_full", "message": str(exc)}}
        )


# ── schemas ────────────────────────────────────────────────────────────
class ExecuteBody(BaseModel):
    session_id: str
    language: str
    code: str
    timeout_seconds: int | None = None


class ReadBody(BaseModel):
    session_id: str
    path: str


class WriteBody(BaseModel):
    session_id: str
    path: str
    content: str
    mode: str = "overwrite"


class UploadBody(BaseModel):
    session_id: str
    path: str
    content_b64: str
    mode: str = "overwrite"


class EditBody(BaseModel):
    session_id: str
    path: str
    find: str
    replace: str
    occurrence: str = "first"


class DeleteBody(BaseModel):
    session_id: str
    path: str


class ListBody(BaseModel):
    session_id: str
    path: str | None = None


class SearchBody(BaseModel):
    session_id: str
    pattern: str
    path: str | None = None
    regex: bool = False
    offset: int = 0
    limit: int = 200


class CloneBody(BaseModel):
    from_session_id: str
    to_session_id: str


# ── routes ─────────────────────────────────────────────────────────────
@app.post("/execute", dependencies=[Depends(require_token)])
def execute(body: ExecuteBody):
    _quota_guard(body.session_id)
    try:
        with limits.exec_slot(body.session_id):
            return docker_runner.execute(
                body.session_id, body.language, body.code, body.timeout_seconds
            )
    except limits.SandboxBusy as exc:
        raise HTTPException(
            status_code=429,
            detail={"error": {"code": "sandbox_busy", "message": str(exc)}},
            headers={"Retry-After": "2"},
        )
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": {"code": "bad_request", "message": str(exc)}})


@app.post("/files/read", dependencies=[Depends(require_token)])
def files_read(body: ReadBody):
    try:
        return fs_ops.read_file(body.session_id, body.path)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/files/write", dependencies=[Depends(require_token)])
def files_write(body: WriteBody):
    _quota_guard(body.session_id, len(body.content.encode("utf-8")))
    try:
        return fs_ops.write_file(body.session_id, body.path, body.content, body.mode)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/files/upload", dependencies=[Depends(require_token)])
def files_upload(body: UploadBody):
    try:
        raw = base64.b64decode(body.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "bad_request", "message": "content_b64 is not valid base64"}},
        )
    _quota_guard(body.session_id, len(raw))
    try:
        return fs_ops.write_bytes(body.session_id, body.path, raw, body.mode)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/files/edit", dependencies=[Depends(require_token)])
def files_edit(body: EditBody):
    try:
        return fs_ops.edit_file(body.session_id, body.path, body.find, body.replace, body.occurrence)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/files/delete", dependencies=[Depends(require_token)])
def files_delete(body: DeleteBody):
    try:
        return fs_ops.delete_file(body.session_id, body.path)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/files/list", dependencies=[Depends(require_token)])
def files_list(body: ListBody):
    try:
        return fs_ops.list_files(body.session_id, body.path)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/files/search", dependencies=[Depends(require_token)])
def files_search(body: SearchBody):
    try:
        return fs_ops.search_files(
            body.session_id, body.pattern, body.path, body.regex, body.offset, body.limit
        )
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.post("/workspace/clone", dependencies=[Depends(require_token)])
def workspace_clone(body: CloneBody):
    """Phase 18: mirror one workspace into another (QA's isolated checkout)."""
    try:
        return fs_ops.clone_workspace(body.from_session_id, body.to_session_id)
    except fs_ops.InvalidPath as exc:
        raise _bad_path(exc)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "docker": docker_runner.docker_status(),
        "egress_filter": network_guard.status(),
        "exec": limits.stats(),
    }
