"""Application settings, loaded from environment / .env (see SPEC §11.1)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # provider keys
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None

    # tools
    tavily_api_key: str | None = None  # enables the web_search tool

    # chat file uploads / attachments
    max_upload_mb: int = 20
    upload_dir: str = "./data/uploads"

    # pull the latest litellm price map from the network on boot (see
    # observability/tracing.estimate_cost). Off by default — it's a network call.
    litellm_refresh_model_cost: bool = False

    # core
    database_url: str = "sqlite:///./data/pagi.db"
    session_secret: str = "dev-insecure-secret-change-me-0123456789abcdef0123456789abcdef"

    # sandbox
    sandbox_url: str = "http://sandbox:8000"
    sandbox_internal_token: str = "dev-internal-token"

    # web
    cors_origins: str = "http://localhost:5173"

    # misc
    approval_timeout_hours: int = 24
    log_level: str = "INFO"
    timezone: str = "Asia/Ho_Chi_Minh"

    # ── reliability (Wave 2) ────────────────────────────────────────────
    # hard ceiling on a single unattended agent turn (cron / REST). Interactive
    # turns are not whole-turn capped (a human may take a while to approve) —
    # llm_call_timeout_seconds bounds each provider call instead.
    turn_max_seconds: int = 900
    llm_call_timeout_seconds: int = 300
    # on boot, resume at most this many interrupted turns concurrently.
    resume_sweep_concurrency: int = 4
    # a session whose turn_status has been "running" for longer than this on
    # boot is considered interrupted (crash mid-stream) and resumed.
    resume_stale_after_seconds: int = 120

    # ── sandbox concurrency / quota (Wave 2, sandbox side reads its own) ─
    sandbox_max_concurrent_calls: int = 6

    # ── prompt caching / context (Wave 3) ──────────────────────────────
    # reserve this many tokens of headroom below the model's context window
    # when trimming history to fit.
    context_margin_tokens: int = 2000
    # summarise history that falls out of the context budget into a rolling
    # system note instead of dropping it silently.
    enable_rolling_summary: bool = True
    # model used for the rolling summary + (if set) title generation; blank =
    # reuse the conversation's own agent/model.
    summary_model: str | None = None

    # ── semantic memory (Wave 3) — feature is off unless this is set ────
    embedding_model: str | None = None  # e.g. "text-embedding-3-small"
    memory_top_k: int = 4
    memory_cross_session: bool = True  # retrieve from the user's other chats too

    # ── RAG / Knowledge Base (Wave 6 / Phase 12) — SPEC §14.  These are the
    # system-wide defaults (lowest cascade tier); a collection or an agent
    # overrides them via its own config without a restart.
    rag_enabled: bool = True
    rag_embedding_model: str = "text-embedding-3-small"
    rag_vector_store: str = "sqlite_np"  # sqlite_np | pgvector (seam)
    rag_max_doc_mb: int = 25
    rag_max_chunks_per_collection: int = 100_000
    rag_ingest_concurrency: int = 2
    rag_enrich_concurrency: int = 4  # per-chunk LLM calls during enrichment
    rag_embed_max_retries: int = 3
    rag_embed_batch_size: int = 64
    rag_query_log_retention_days: int = 30
    rag_default_config_json: str = "{}"  # JSON override of RagConfig §14.2

    # ── sub-agent / delegation (Phase 13, SPEC §15 / §11.1) ──────────
    # infrastructure-level safety ceilings — deliberately NOT in
    # OrchestrationConfig: a user tunes config from the UI, but must not be able
    # to raise the system's own cost ceiling from the UI.
    subagent_enabled: bool = True
    max_subagent_depth: int = 2
    max_subagents_per_turn: int = 5
    subagent_timeout_seconds: int = 600
    subagent_usd_budget_per_turn: float = 0.5  # 0 = off
    subagent_parallel_limit: int = 3

    # ── orchestration (Phase 14, SPEC §16 / §11.1) ──────────────────
    orch_default_config_json: str = "{}"
    orch_max_total_llm_calls: int = 40
    orch_node_timeout_seconds: int = 180
    orch_run_log_retention_days: int = 30

    # ── usage budget (Wave 5) — 0 disables the warning ────────────────
    monthly_budget_usd: float = 0.0

    # ── optional Redis (Wave 1c seam) — unset = in-process fallback ────
    redis_url: str | None = None

    # set true in production (HTTPS behind reverse proxy) so the session cookie
    # gets the Secure attribute; keep false for plain-http local dev.
    session_cookie_secure: bool = False

    # seed admin
    admin_username: str = "admin"
    admin_password: str = "admin"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def async_database_url(self) -> str:
        """Return an async-driver DSN (sqlite -> aiosqlite, postgres -> asyncpg)."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
