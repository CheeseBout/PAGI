"""Dev entrypoint.

After activating the virtualenv, from the ``sandbox/`` directory:

    python main.py

is equivalent to ``uvicorn app:app --reload --port 8100``. It loads
``sandbox/.env`` (if present) before importing the app, so ``config.py`` sees
those values.

Env overrides: HOST (default 127.0.0.1), PORT (8100), RELOAD (true).
For production, run ``uvicorn app:app`` behind a process manager instead (this is
what the Dockerfile does).
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    """Minimal KEY=value .env loader (no extra dependency). Existing env wins."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def main() -> None:
    _load_env()  # must run before `app`/`config` are imported
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8100"))
    reload = os.getenv("RELOAD", "true").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run("app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
