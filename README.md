# PAGI — Self-hosted Single-tenant AI Agent Gateway

A self-hosted AI agent gateway for one person / one org. Chat UI in the style of
ChatGPT/Claude/Gemini, four LLM providers behind one interface, a tool layer with
an isolated code/file **sandbox**, a **Knowledge Base / RAG** layer (per-collection
tunable retrieval), and **human-in-the-loop (HITL)** approval for every risky action.

Above that sits an orchestration layer: agents can **delegate to sub-agents**
(a real session tree, so a sub-agent inherits HITL, crash recovery and cost
accounting for free) and run under a selectable **reasoning pattern** — ReAct,
Plan-Execute, Reflexion, Router, Supervisor, Debate, or Evaluator-Optimizer —
with an A/B split to compare two patterns on live traffic.

The authoritative contracts live in the code: DB schema in
`backend/app/db/models.py`, settings in `backend/app/config.py`, REST routes in
`backend/app/api/`, the sandbox HTTP API in `sandbox/app.py`.

---

## Repository layout

```
backend/    FastAPI — API gateway, agent runtime, providers, tools, HITL, scheduler
frontend/   React + Vite + TypeScript SPA (chat UI, approval cards, model picker)
sandbox/    FastAPI — runs agent code/file ops in ephemeral Docker containers
desktop/    Electron shell for the floating overlay (optional, Windows) — see "Desktop overlay"
docker/     docker-compose.yml wiring all three together
```

Boundaries are deliberate: `backend/` never executes agent code — it calls
`sandbox/` over an internal network. `frontend/` imports no Python. `sandbox/` is
the only component that touches untrusted code.

---

## Quick start — local dev (no Docker)

Prerequisites: Python 3.12+, Node 20+. (Docker optional; without it the sandbox
falls back to running code as a local subprocess — dev only, not isolated.)

### 1. Backend

```bash
cd backend
python -m venv venv && . venv/bin/activate        # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                               # then edit: add provider API key(s), set SESSION_SECRET
python main.py                                     # ⇔ uvicorn app.main:app --reload --port 8000
```

`python main.py` is the dev entrypoint (honours `HOST` / `PORT` / `RELOAD` env
vars). On first boot it runs the Alembic migrations to create `data/pagi.db`,
seeds an admin user (`ADMIN_USERNAME`/`ADMIN_PASSWORD`, default `admin`/`admin`)
and four sample agents (one per provider). For production run
`uvicorn app.main:app` behind a process manager instead.

### 2. Sandbox

```bash
cd sandbox
python -m venv venv && . venv/bin/activate         # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                               # SANDBOX_INTERNAL_TOKEN must match backend/.env
python main.py                                     # ⇔ uvicorn app:app --reload --port 8100
```

`python main.py` loads `sandbox/.env` and honours `HOST` / `PORT` / `RELOAD`.
For local dev on Windows/Mac set `WORKSPACE_ROOT=./workspaces` in `sandbox/.env`
(the `/workspaces` default is the in-container path). For production run
`uvicorn app:app` behind a process manager instead.

Set `SANDBOX_URL=http://localhost:8100` in `backend/.env`.
For real container isolation also build the exec image:

```bash
docker build -t pagi-sandbox:latest -f sandbox/images/Dockerfile.sandbox sandbox/images
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173  (proxies /api and /ws to :8000)
```

Open http://localhost:5173 and sign in with `admin` / `admin`.

### CLI smoke test (providers only)

```bash
cd backend
python -m app.cli --provider anthropic --model claude-haiku-4-5
```

---

## Quick start — Docker

```bash
cp backend/.env.example  backend/.env       # edit provider keys + SESSION_SECRET
cp sandbox/.env.example  sandbox/.env       # SANDBOX_INTERNAL_TOKEN must match backend/.env
docker build -t pagi-sandbox:latest -f sandbox/images/Dockerfile.sandbox sandbox/images
docker compose -f docker/docker-compose.yml up -d --build
```

App: http://localhost:8080 (Nginx serves the SPA and reverse-proxies `/api` +
`/ws` to the backend). For production put a TLS terminator (Caddy / Nginx +
Let's Encrypt) in front and set `SESSION_COOKIE_SECURE=true` and `CORS_ORIGINS`
to your domain in `backend/.env`.

---

## Environment variables

Every setting, with its default and an explanatory comment, is in
`backend/app/config.py` and `sandbox/config.py`; `.env.example` in each service
mirrors them. The essentials:

| File | Key | Notes |
|---|---|---|
| `backend/.env` | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` | at least the one(s) your agents use |
| | `TAVILY_API_KEY` | optional — enables the `web_search` tool |
| | `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| | `DATABASE_URL` | `sqlite:///./data/pagi.db` default; `postgresql://…` supported |
| | `SANDBOX_URL`, `SANDBOX_INTERNAL_TOKEN` | must line up with `sandbox/.env` |
| | `CORS_ORIGINS`, `SESSION_COOKIE_SECURE` | set for your deployment |
| | `MAX_UPLOAD_MB`, `UPLOAD_DIR` | chat file uploads (default 20 MB, `./data/uploads`) |
| `sandbox/.env` | `SANDBOX_INTERNAL_TOKEN` | shared secret with the backend |
| | `SANDBOX_MEMORY_LIMIT` / `SANDBOX_CPU_LIMIT` / `SANDBOX_PIDS_LIMIT` / `SANDBOX_TIMEOUT_SECONDS` | ephemeral-container limits |
| `frontend/.env` | `VITE_API_BASE_URL`, `VITE_WS_BASE_URL` | leave as `/api` `/ws` for same-origin |

---

## Tools & HITL

Built-in tools (extendable via MCP servers, registered in the UI/API):

| Tool | Default policy | Runs in |
|---|---|---|
| `fetch_url`, `web_search`, `get_current_datetime`, `read_file`, `list_files`, `search_files` | **auto** | backend / sandbox fs |
| `http_request`, `execute_code`, `write_file`, `edit_file`, `delete_file` | **ask** (HITL) | backend / sandbox |

`web_search` (Tavily) is only offered to agents when `TAVILY_API_KEY` is set;
without it the tool returns an error instead of running.

`ask` tools pause the turn; the UI shows an **ApprovalCard** inline in the chat
with the tool name + args (`write_file`/`edit_file`/`delete_file` render a
before/after **diff**; code as a code block). Approve → the tool runs and the
turn continues; **Approve & always allow** → same, and the tool runs
unprompted for the rest of that conversation (a `session_tool_grants` row,
shown as a revocable chip above the chat); Deny → the agent gets
`{"error": "user_denied"}`. Per-agent `tool_policy` can override any default.
Cron jobs run *unattended*: `ask` tools are stripped unless the job lists them
in `unattended_allowed_tools`.

Agents, cron jobs and MCP servers are editable in the **Settings** screen
(gear icon in the sidebar), which also has a **Usage** tab (cost/token
aggregation by day/model/conversation + monthly budget). Each conversation has
a **🗂 Files** toggle that browses the session's sandbox workspace.

---

## 2D avatar (optional, SPEC §20, `2D_PLAN.md`)

Any agent can show a Live2D avatar beside the chat, driven by conversation
state (idle/thinking/talking/acting/waiting — `frontend/src/hooks/useAvatarState.ts`).
Off by default. Rendering goes through the official **Cubism Web Framework**,
vendored into `frontend/src/vendor/live2d/` (not a third-party wrapper — an
earlier version used `pixi-live2d-display`, which never caught up to Cubism
5; see `2D_PLAN.md` §3 for why that got replaced).

1. **Cubism Core is proprietary** (Live2D Inc.) and can't be distributed via
   npm — download it yourself from the official
   [Cubism SDK for Web](https://www.live2d.com/en/sdk/download/web/) and place
   `Core/live2dcubismcore.min.js` at
   `frontend/public/live2d/live2dcubismcore.min.js`. Missing this file is
   safe: the avatar shows a static placeholder instead of crashing.
2. Get a model (any Cubism 3/4-format model — `*.model3.json` + its
   textures/motions; the SDK above bundles 8 free samples under
   `Samples/Resources/`) and add it to the **shared library** — one upload,
   every agent's picker sees it, no per-agent setup:
   - **Upload from the UI**: Settings → Agents → edit any agent (even one
     you haven't saved yet) → **Avatar (2D)** → turn it on → **Upload
     folder**, then pick the model's folder (the one containing its
     `*.model3.json`) — the browser sends every file in it, no zipping
     needed. Lands in `backend/data/avatars/_shared/<model folder>/`.
   - **Place it by hand** at `backend/data/avatars/_shared/<model folder>/`
     if you'd rather not go through the browser.
   - (Less common) a model can also be uploaded/placed **private to one
     agent** at `backend/data/avatars/<agent_id>/<model folder>/` instead —
     it shadows a same-named shared model for that agent only; everyone else
     still sees the shared version.
3. Pick the model from the **Model** dropdown (populated from whatever's
   actually in the shared library + that agent's own directory — no typing a
   path by hand) → **Save**.

Avatar assets are served unauthenticated at `GET /avatars/{agent_id}/...`
(`backend/app/api/routes_avatar_files.py`) — an accepted risk for a
single-tenant deployment behind an internal reverse proxy, same posture as the
Docker-socket note above. The upload endpoints themselves
(`POST /avatar-library/upload`, `POST /agents/{id}/avatar/upload`) *are*
behind the normal session auth.
Text-to-speech / real audio lip-sync is not implemented yet; `AvatarCanvas`'s
`audioLevel` prop is the single point where a future TTS phase plugs in (see
`2D_SPEC.md` §20.11).

## Desktop overlay (optional, Windows)

A small floating window: the agent's 2D avatar over your desktop, an input box under
it, speech bubbles for short replies, and a panel for longer ones. It is one more
client of the same backend — one continuous chat per agent (the full history is in the
web UI, tagged "Overlay") — so approvals, tools and cost tracking behave exactly as on
the web. Off by default; nothing about it runs unless you start it.

```bash
# backend and the frontend dev server running as above, then:
cd desktop
npm install
npm run dev            # or: npm run build && npm start
```

You start the backend yourself; the overlay just connects to it (and keeps retrying
with backoff if it isn't up). **Log in once inside the overlay** — the window keeps its
own cookie jar, so being logged in on the web doesn't carry over. Tray icon → "Đăng
xuất" signs out.

| | |
|---|---|
| Move | drag the avatar |
| Ask | type in the box, Enter to send |
| Panel / history | the panel button (or click a bubble); ↗ opens the full chat on the web |
| Hide / show | `Ctrl+Alt+P` (`PAGI_OVERLAY_HOTKEY_TOGGLE`) or the tray icon |
| Screenshot | the crop button, `Ctrl+Alt+X` (`PAGI_OVERLAY_HOTKEY_CAPTURE`), or type `/screen` (optionally followed by your question) |

**Screenshots and privacy.** A capture only ever starts from one of those three things
you do — never on a timer, never because the model asked. You drag a region, see a
preview, and it is uploaded **only when you press send**; the desktop app keeps it in
memory and writes nothing to disk. Once sent it is stored like any attachment and goes
to the LLM provider of the agent you're talking to (the agent needs vision enabled).

**Approvals and notifications.** A tool that needs approval shows an approval card in
the bubble; if the overlay is hidden you get a system toast (it names the tool but
never shows the arguments — click it to see the card). Finished cron runs and project
iterations also raise a toast. Notifications are not stored: anything that happens while
the overlay isn't running is only in the web UI. Cron jobs and project loops keep
running in the backend either way.

**Resources.** The avatar renders at most 30 fps, drops to 15 fps after 30 s of
inactivity, and stops entirely while the window is hidden. Memory is dominated by the
**model's textures** (each RGBA pixel is 4 bytes of GPU memory): a model with 2048×2048
textures costs tens of MB, one with several 4096×4096 ones hundreds, and a 16384×16384
one about a gigabyte — pick or downscale models accordingly.

Settings (environment variables read by the desktop app): `PAGI_OVERLAY_URL` (default
`http://localhost:5173`; use the same origin as the API, and https if it isn't
localhost), `PAGI_OVERLAY_HOTKEY_TOGGLE`, `PAGI_OVERLAY_HOTKEY_CAPTURE`. If
`ELECTRON_RUN_AS_NODE` is set in your shell (VS Code's terminal sets it), use
`npm run dev`/`npm start`, which clear it. Spec: `SPEC.md` §21. Automated checks:
`desktop/e2e/README.md`.

---

## Security

- Passwords hashed with Argon2id; session = HS256 JWT in an `httponly` cookie.
- Login rate-limited (5 failures / 15 min / IP); chat is rate-limited too
  (30 turns/min/user, REST and WebSocket both) to blunt runaway loops/abuse.
- `fetch_url` / `http_request` / RAG URL ingest run through an SSRF guard (blocks
  private/loopback/link-local and `169.254.169.254`) via a shared
  `core/security.py::safe_async_client`, which re-validates every redirect hop
  too — a bare pre-flight check on the original URL would otherwise let a 302
  to a private address slip through `follow_redirects=True`.
- Tool output (web pages, HTTP responses, command output, MCP tool results) is
  scanned for basic prompt-injection markers and logged for audit
  (`core/security.py::scan_for_injection`, walked recursively over the whole
  result value rather than a fixed field-name list, with NFKC normalization +
  zero-width-character stripping so simple unicode obfuscation doesn't dodge
  it); every agent's system prompt also gets an explicit "tool output is data,
  not instructions" reminder appended (`core/memory.py`). Heuristic only — the
  real control is HITL gating every side-effecting tool.
- Sandbox path handling rejects `..` escapes and absolute paths, and resolves
  symlinks before the containment check (`sandbox/fs_ops.py::resolve`).
- Pending approvals older than `APPROVAL_TIMEOUT_HOURS` (default 24h) are swept
  to `expired` every 15 minutes (`backend/app/scheduler/cron_jobs.py`).
- **Docker socket**: mounting `/var/run/docker.sock` into the sandbox service (as
  `docker/docker-compose.yml` does) is **root-equivalent access to the host**.
  For a real deployment run `sandbox/` on a separate VM/host, or accept the risk
  for a personal single-machine setup. Without the socket the service uses the
  **unsafe subprocess fallback** — fine for local dev only.
- **Egress filtering**: ephemeral exec containers run on a dedicated Docker
  network (`pagi-exec`); on startup the sandbox service best-effort installs
  `DOCKER-USER` iptables rules dropping traffic from that network to
  RFC1918/loopback/link-local (covers the `169.254.169.254` metadata IP) while
  leaving outbound Internet untouched (`sandbox/network_guard.py`). **Linux
  Docker hosts only** — on Docker Desktop (Windows/Mac) this degrades to
  `"unavailable"` rather than failing the service; check `GET /health` on the
  sandbox (`egress_filter: "active" | "unavailable" | "disabled"`). Disable via
  `SANDBOX_ENABLE_EGRESS_FILTER=false`.
- **Container cleanup**: every exec container is labelled `pagi.sandbox=exec`;
  on startup the sandbox force-removes any left over from a crashed previous
  run (`docker_runner.reap_orphans`), on top of the per-request `finally: remove()`.

---

## Known limitations (MVP)

- Approval waiters live in process memory. If the backend restarts mid-approval,
  the pending row is resumed by a fresh background turn when you approve/deny —
  agent state is always rebuilt from the DB, so nothing is lost. On boot a
  **resume sweep** also re-runs any turn whose session was still `running`
  (see `sessions.turn_status`) when the process died.
- `search_files` returns up to `limit` results (default/max 200/500) starting
  at `offset`, capped at 2000 total matches scanned per call; `has_more`
  indicates whether a further page exists.
- One shared sandbox service for all sessions. It now caps concurrent exec
  containers (`SANDBOX_MAX_CONCURRENT_EXEC`), serialises exec per session, and
  enforces a per-workspace byte quota (`SANDBOX_WORKSPACE_QUOTA_MB`) — but there
  is still no fair-share queue across sessions.
- `traces.cost_usd` comes from litellm's bundled price map
  (`litellm.model_cost`), refreshed on litellm upgrade or by setting
  `LITELLM_REFRESH_MODEL_COST=true`; unknown models fall back to a small static
  table in `app/observability/tracing.py`, then `null`. Prompt-cache reads/writes
  are applied as a rough correction (`traces.cached_tokens` /
  `cache_write_tokens` record the raw counts).
- Chat images are downscaled to ≤1568 px on upload and the base64 data URI is
  cached per file, but history is still re-sent every turn; on Anthropic /
  Claude-via-OpenRouter the system prompt is marked with `cache_control` so the
  stable prefix is billed at the cache-read rate. Only images + small (≤20 KB)
  text files reach the model; vision-incapable models get a non-fatal warning.
- Long conversations are trimmed to the model's context window before each call;
  the dropped prefix is folded into a rolling summary
  (`ENABLE_ROLLING_SUMMARY`, `sessions.summary`).
- Semantic recall across past conversations is available but **off unless
  `EMBEDDING_MODEL` is set** (embeddings stored in `memory_chunks`, cosine
  search in Python).
- Schema is managed by **Alembic** (`backend/alembic`); `alembic upgrade head`
  runs in-process on startup, and a pre-Alembic dev DB is migrated + stamped
  automatically. Tests still use `create_all` (`PAGI_SKIP_ALEMBIC=1`).
- Multi-worker (Redis-backed rate-limit / WS fan-out / approval registry) is not
  implemented; `REDIS_URL` is reserved but unused.
- The egress filter (see Security) only takes effect on a real Linux Docker
  host — Docker Desktop deployments run without it.
- Prompt-injection handling is a heuristic keyword scan + a system-prompt
  reminder, not a hard control — treat it as an audit signal, not a defense.

---

## What the UI can do

- Streaming chat with markdown/code rendering, stop/abort mid-generation.
- Attach files to a message (📎 / drag-drop / paste): images go to vision-capable
  models inline, small text files are inlined as text, and any file can be pushed
  into the session's sandbox workspace for the tools to use.
- Inline HITL **ApprovalCard** (approve/deny) synced across every open tab.
- **Regenerate** the last answer, and **edit** any previously sent message
  (both drop everything after that point and re-run the turn — like ChatGPT).
- **Load earlier messages** (cursor pagination — `GET /conversations/{id}?before=`)
  and cursor-paginated conversation list (`GET /conversations?before=`).
- A small dot on cron-run conversations in the sidebar until you open them
  (tracked client-side in `localStorage`, not a server-side "unread" column).

---

## Tests

```bash
cd backend  && pip install -r requirements.txt && pip install pytest pytest-asyncio && pytest
cd sandbox  && pip install -r requirements.txt && pip install pytest && pytest
cd frontend && npm install && npm test          # vitest unit tests
cd frontend && npx tsc --noEmit                 # type check
```

End-to-end tests (`frontend/tests/e2e/`) drive a real browser and do **not**
start anything themselves — bring up the backend (:8000), sandbox (:8100) and
`npm run dev` (:5173) first, then:

```bash
cd frontend && npx playwright install chromium   # once
cd frontend && npm run test:e2e
```

No provider API key is needed for the unit suites: every test that would call an
LLM injects a scripted fake provider or a `FakeJudge`.

### What the suites cover

| Area | Files | Focus |
|---|---|---|
| Auth & security | `test_security.py`, `test_rate_limit_and_injection.py`, `test_tool_output_scan.py` | Argon2 hashing, SSRF guard incl. redirect hops, login/chat rate limits, recursive prompt-injection scan (nested values, zero-width + unicode obfuscation) |
| API surface | `test_api_flow.py`, `test_pagination.py`, `test_files.py` | auth → agents → conversation → approvals, cursor pagination, upload/retrieval |
| Agent runtime | `test_hitl.py`, `test_truncate.py`, `test_expiry_sweep.py`, `test_wave_upgrades.py` | HITL waiter, regenerate/edit truncation, approval expiry, resume sweep, context trimming, session tool grants |
| Tools & cost | `test_tools.py`, `test_web_search.py`, `test_pricing.py`, `test_openrouter_models.py` | registry + policy resolution (incl. unattended), mocked Tavily, litellm cost estimation with static fallback |
| RAG | `test_rag_unit.py`, `test_rag_flow.py`, `test_rag_eval.py` | config cascade, chunkers, context packer, vector store + BM25, RRF fusion, MMR rerank, query transforms, Corrective RAG, enrichment, RAGAS metrics vs. a FakeJudge |
| Sub-agents & patterns | `test_subagent.py`, `test_patterns.py`, `test_config_schema.py` | event bubbling, `delegate_task` idempotency, depth/budget ceilings, Reflexion/Router/Supervisor strategies, pattern A/B arm recording |
| Agent eval | `test_agent_eval.py` | trajectory metrics (redundant calls, forbidden tools, step efficiency), background run lifecycle, usage split by origin |
| Sandbox | `sandbox/tests/` | file ops incl. binary writes, path-traversal blocking, concurrency/quota guards, egress filter degrading safely without Docker |
| 2D avatar | `test_avatar.py` | avatar_config validation, `avatar_ready` derivation, static-file route (nosniff header, path-traversal, unknown agent), model listing, folder-upload endpoint (extension allowlist, path traversal, single-top-level-folder / single-model3.json rules, per-subfolder overwrite), shared library (upload/list, own-copy-shadows-shared resolution) |
| Frontend | `src/**/*.test.ts` | WebSocket URL building, live-event reducer, line diff, avatar state machine transitions |

> Last run on this machine: **backend 207 passed** (52s), **sandbox 18 passed**,
> **frontend 27 passed**. E2E not run in that pass — it needs all three services
> up. Re-run before trusting after further changes.

### Not yet in place

There is no CI pipeline — `ruff` is configured in `backend/pyproject.toml` but
nothing enforces it, and nothing runs the suites on push. Python dependencies are
unpinned (`>=` ranges, no lockfile), so two builds a week apart can resolve to
different versions; `frontend/package-lock.json` does pin the JS side. Frontend
unit coverage is thin relative to its ~5k lines of components — the e2e specs
carry most of the UI verification.
