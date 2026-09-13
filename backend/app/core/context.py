"""Fit conversation history into the model's context window (Wave 3c).

The agent loop rebuilds the full history from the DB every iteration. For a long
conversation that eventually overflows the model's context window and the turn
dies with a provider 400. This module trims the history to the most recent
messages that fit; when ``ENABLE_ROLLING_SUMMARY`` is on, the dropped prefix is
condensed into a single note stored on the session so its content isn't lost.

The trim is a safety measure, not a billing calculation — token counts here are
deliberately cheap estimates.
"""

from __future__ import annotations

import structlog

from ..config import get_settings
from ..db.models import Agent, ChatSession, Message

log = structlog.get_logger("pagi.context")

_CHARS_PER_TOKEN = 4
_DEFAULT_WINDOW = 128_000


def _model_window(model: str) -> int:
    try:
        import litellm

        info = litellm.get_model_info(model) or {}
        return int(info.get("max_input_tokens") or info.get("max_tokens") or _DEFAULT_WINDOW)
    except Exception:
        return _DEFAULT_WINDOW


def budget_tokens(model: str, max_output: int, *, reserve: int = 0) -> int:
    margin = get_settings().context_margin_tokens
    return max(2_000, _model_window(model) - max(0, max_output) - margin - max(0, reserve))


def _est(m: Message) -> int:
    n = len(m.content or "") // _CHARS_PER_TOKEN
    for tc in m.tool_calls or []:
        n += 12 + len(str(tc.get("args") or "")) // _CHARS_PER_TOKEN
    for a in m.attachments or []:
        # inlined images dominate; assume ~1.2k tokens each, text handled via content
        n += 1_200 if a.get("kind") == "image" else 0
    return n + 4


def _est_all(system_prompt: str, messages: list[Message]) -> int:
    return len(system_prompt or "") // _CHARS_PER_TOKEN + sum(_est(m) for m in messages)


def _cut_index(messages: list[Message], budget: int) -> int:
    """Smallest index such that ``messages[index:]`` fits ``budget`` while keeping
    assistant/tool-result pairs intact and never dropping the last user turn."""
    total = 0
    cut = 0
    for i in range(len(messages) - 1, -1, -1):
        total += _est(messages[i])
        if total > budget:
            cut = i + 1
            break
    # don't start the kept slice on an orphaned tool result
    while cut < len(messages) and messages[cut].role == "tool":
        cut += 1
    last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=0)
    return min(cut, last_user)


async def _summarise(agent: Agent, dropped: list[Message], prior: str | None) -> str | None:
    from ..providers import DoneEvent, TextDelta, UsageEvent, get_provider

    settings = get_settings()
    transcript_lines: list[str] = []
    for m in dropped:
        text = (m.content or "").strip()
        if not text and m.tool_calls:
            text = "(called tools: " + ", ".join(tc.get("name", "?") for tc in m.tool_calls) + ")"
        if text:
            transcript_lines.append(f"{m.role}: {text[:2000]}")
    transcript = "\n".join(transcript_lines)[:16_000]
    if not transcript:
        return prior

    instruction = (
        "You are compressing the earlier part of a conversation so it can be "
        "dropped from context. Produce a terse factual summary (<= 200 words) "
        "capturing decisions, facts, file paths, and open threads. Do not add "
        "commentary."
    )
    if prior:
        instruction += "\n\nExisting summary to extend:\n" + prior

    provider = get_provider(agent.provider)
    parts: list[str] = []
    try:
        async for ev in provider.stream_chat(
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": transcript},
            ],
            tools=[],
            model=settings.summary_model or agent.model,
            temperature=0.0,
            max_tokens=512,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, (DoneEvent, UsageEvent)):
                continue
    except Exception as exc:  # pragma: no cover - external
        log.warning("summary_failed", error=str(exc))
        return prior
    return "".join(parts).strip() or prior


async def prepare(
    db, chat: ChatSession, agent: Agent, messages: list[Message], *, reserve_tokens: int = 0
) -> tuple[list[Message], str | None]:
    """Return (messages_to_send, rolling_summary_or_None).

    ``reserve_tokens`` is subtracted from the window first — the agent runtime
    passes the size of the retrieved RAG context so history is trimmed against
    what's actually left (SPEC §14.6)."""
    budget = budget_tokens(agent.model, agent.max_tokens, reserve=reserve_tokens)
    if _est_all(agent.system_prompt, messages) <= budget:
        return messages, chat.summary

    cut = _cut_index(messages, budget)
    if cut <= 0:
        return messages, chat.summary

    dropped, kept = messages[:cut], messages[cut:]
    summary = chat.summary
    settings = get_settings()
    if settings.enable_rolling_summary and dropped and chat.summary_upto_message_id != dropped[-1].id:
        summary = await _summarise(agent, dropped, prior=chat.summary)
        chat.summary = summary
        chat.summary_upto_message_id = dropped[-1].id
        db.add(chat)
        await db.commit()
    log.info(
        "context_trimmed",
        session_id=chat.id,
        dropped=len(dropped),
        kept=len(kept),
        summarised=bool(summary),
    )
    return kept, summary
