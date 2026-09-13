"""Minimal CLI chat for smoke-testing providers (PLAN Phase 1).

    python -m app.cli --provider anthropic --model claude-haiku-4-5

Streams tokens to stdout. No tools, no persistence — just the provider adapter.
"""

from __future__ import annotations

import argparse
import asyncio

from .providers import DoneEvent, TextDelta, UsageEvent, get_provider


async def _chat(provider_name: str, model: str, system: str) -> None:
    provider = get_provider(provider_name)
    history: list[dict] = []
    if system:
        history.append({"role": "system", "content": system})
    print(f"[{provider_name}:{model}] — type 'exit' to quit\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if user in ("exit", "quit"):
            return
        if not user:
            continue
        history.append({"role": "user", "content": user})
        print("bot> ", end="", flush=True)
        parts: list[str] = []
        async for ev in provider.stream_chat(
            messages=history, tools=[], model=model, temperature=0.7, max_tokens=2048
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
                print(ev.content, end="", flush=True)
            elif isinstance(ev, UsageEvent):
                pass
            elif isinstance(ev, DoneEvent):
                break
        print("\n")
        history.append({"role": "assistant", "content": "".join(parts)})


def main() -> None:
    ap = argparse.ArgumentParser(description="PAGI provider smoke-test CLI")
    ap.add_argument("--provider", default="anthropic",
                    choices=["openai", "anthropic", "gemini", "openrouter"])
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--system", default="You are a helpful assistant.")
    args = ap.parse_args()
    asyncio.run(_chat(args.provider, args.model, args.system))


if __name__ == "__main__":
    main()
