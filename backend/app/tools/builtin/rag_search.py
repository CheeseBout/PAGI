"""``rag_search`` — retrieve from the agent's Knowledge Base (SPEC §5.2, §14.5).

Read-only, so policy ``auto``. This is how an agent *decides* to look something
up (mode ``tool`` / ``auto``); mode ``always`` injects context without it. The
agent has no tool to write to the knowledge base.
"""

from __future__ import annotations

from ...db.models import Agent, ChatSession
from ..base import ToolContext, ToolSpec, register


async def _handler(ctx: ToolContext, args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required", "chunks": []}

    chat = await ctx.db.get(ChatSession, ctx.session_id)
    agent = await ctx.db.get(Agent, chat.agent_id) if chat is not None else None
    if agent is None:
        return {"error": "agent not found", "chunks": []}

    from ...rag import service

    if not service.rag_globally_enabled():
        return {"error": "RAG is disabled (RAG_ENABLED=false)", "chunks": []}

    return await service.search_for_tool(
        agent,
        session_id=ctx.session_id,
        query=query,
        collection_ids=args.get("collection_ids") or None,
        top_k=args.get("top_k"),
    )


SPEC = register(
    ToolSpec(
        name="rag_search",
        description=(
            "Search the knowledge base attached to this agent and return the most "
            "relevant document passages (with a citable doc id) plus a packed "
            "context block. Read-only. Use it whenever the answer might live in the "
            "user's own documents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look up."},
                "collection_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these collections (default: all the agent's).",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many passages to return (default from config).",
                },
            },
            "required": ["query"],
        },
        handler=_handler,
        requires_approval=False,
    )
)
