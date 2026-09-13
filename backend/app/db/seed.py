"""First-boot seed: one admin user + four sample agents (SPEC Phase 2)."""

from __future__ import annotations

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings
from ..core.security import hash_password
from ..tools import TOOL_REGISTRY
from .models import Agent, User

log = structlog.get_logger("pagi.seed")

_DEFAULT_POLICY = {
    "execute_code": "ask",
    "write_file": "ask",
    "edit_file": "ask",
    "delete_file": "ask",
    "http_request": "ask",
    "fetch_url": "auto",
    "web_search": "auto",
    "get_current_datetime": "auto",
    "read_file": "auto",
    "list_files": "auto",
    "search_files": "auto",
}

_SAMPLE_AGENTS = [
    ("OpenAI · GPT-4o mini", "openai", "gpt-4o-mini"),
    ("Anthropic · Claude Haiku 4.5", "anthropic", "claude-haiku-4-5"),
    ("Gemini · 2.5 Flash-Lite", "gemini", "gemini-2.5-flash-lite"),
    ("OpenRouter · Llama 3.1 8B (free)", "openrouter", "meta-llama/llama-3.1-8b-instruct:free"),
]


async def seed(db: AsyncSession) -> None:
    settings = get_settings()
    all_tools = list(TOOL_REGISTRY.keys())

    if (await db.exec(select(User))).first() is None:
        db.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
            )
        )
        log.info("seeded_admin", username=settings.admin_username)

    if (await db.exec(select(Agent))).first() is None:
        for idx, (name, provider, model) in enumerate(_SAMPLE_AGENTS):
            db.add(
                Agent(
                    name=name,
                    system_prompt="You are a helpful assistant. Explain briefly before running code.",
                    provider=provider,
                    model=model,
                    tools_allowed=all_tools,
                    tool_policy=dict(_DEFAULT_POLICY),
                    is_default=(provider == "openrouter"),
                )
            )
        log.info("seeded_agents", count=len(_SAMPLE_AGENTS))

    await db.commit()
