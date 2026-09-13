"""MCP server registry (SPEC §2.6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import McpServer, User
from ..schemas import McpServerCreate, McpServerPatch
from .deps import APIError, get_current_user, get_db
from .serializers import mcp_out

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp"])
_TRANSPORTS = {"stdio", "sse", "http"}


async def _validate(server: McpServer) -> None:
    from ..tools.mcp_client import _open_session  # noqa: PLC0415

    try:
        async with _open_session(server) as session:
            await session.list_tools()
    except Exception as exc:  # pragma: no cover - external
        raise APIError(422, "mcp_unreachable", f"Could not list tools: {exc}")


@router.get("")
async def list_servers(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [mcp_out(m) for m in (await db.exec(select(McpServer))).all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_server(
    body: McpServerCreate, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    if body.transport not in _TRANSPORTS:
        raise APIError(422, "invalid_transport", f"transport must be one of {sorted(_TRANSPORTS)}")
    server = McpServer(**body.model_dump())
    await _validate(server)
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return mcp_out(server)


@router.patch("/{server_id}")
async def patch_server(
    server_id: str,
    body: McpServerPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    server = await db.get(McpServer, server_id)
    if server is None:
        raise APIError(404, "not_found", "MCP server not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(server, key, value)
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return mcp_out(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    server = await db.get(McpServer, server_id)
    if server is None:
        raise APIError(404, "not_found", "MCP server not found")
    await db.delete(server)
    await db.commit()
