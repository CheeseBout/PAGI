"""Dev entrypoint.

After activating the virtualenv, from the ``backend/`` directory:

    python main.py

is equivalent to ``uvicorn app.main:app --reload --port 8000``. It reads
``backend/.env`` (schema is migrated + the admin user seeded on first boot).

Env overrides: HOST (default 127.0.0.1), PORT (8000), RELOAD (true).
For production, run ``uvicorn app.main:app`` behind a process manager instead.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "true").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
