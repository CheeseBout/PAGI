"""MCP client — loads tools from registered MCP servers (SPEC §5.4).

Best-effort: a server that fails to connect / list is skipped, not fatal.
Every MCP tool defaults to requires_approval=True (external, untrusted).
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import McpServer
from .base import ToolContext, ToolSpec

log = logging.getLogger("pagi.mcp")

NAMESPACE = "mcp__{server}__{tool}"


@asynccontextmanager
async def _open_session(server: McpServer):
    from mcp import ClientSession

    cfg = server.config or {}
    if server.transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env")
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    elif server.transport == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(cfg["url"], headers=cfg.get("headers")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:  # http / streamable-http
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(cfg["url"], headers=cfg.get("headers")) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _make_handler(server: McpServer, remote_name: str):
    async def _handler(ctx: ToolContext, args: dict) -> dict:
        try:
            async with _open_session(server) as session:
                result = await session.call_tool(remote_name, args)
            parts = []
            for item in getattr(result, "content", []) or []:
                text = getattr(item, "text", None)
                parts.append(text if text is not None else json.dumps(getattr(item, "__dict__", {})))
            return {"content": "\n".join(parts), "is_error": bool(getattr(result, "isError", False))}
        except Exception as exc:  # pragma: no cover - external
            return {"error": f"mcp call failed: {exc}"}

    return _handler


async def load_mcp_tools(db: AsyncSession) -> list[ToolSpec]:
    servers = (await db.exec(select(McpServer).where(McpServer.enabled == True))).all()  # noqa: E712
    specs: list[ToolSpec] = []
    for server in servers:
        try:
            async with _open_session(server) as session:
                listed = await session.list_tools()
        except Exception as exc:  # pragma: no cover - external
            log.warning("MCP server %s unavailable: %s", server.name, exc)
            continue
        for tool in listed.tools:
            specs.append(
                ToolSpec(
                    name=NAMESPACE.format(server=server.name, tool=tool.name),
                    description=tool.description or f"MCP tool {tool.name} from {server.name}",
                    parameters=tool.inputSchema or {"type": "object", "properties": {}},
                    handler=_make_handler(server, tool.name),
                    requires_approval=True,
                    tags=["mcp"],
                )
            )
    return specs
