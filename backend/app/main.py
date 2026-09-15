"""FastAPI entrypoint — wires routers, CORS, lifespan (DB + scheduler)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .db.seed import seed
from .db.session import SessionLocal, init_db
from .observability.tracing import configure_logging

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    if settings.litellm_refresh_model_cost:  # pragma: no cover - network, opt-in
        try:
            import litellm

            litellm.model_cost = litellm.get_model_cost_map(url=litellm.model_cost_map_url)
        except Exception:
            pass

    await init_db()
    async with SessionLocal() as db:
        await seed(db)

    from .core.agent_runtime import resume_interrupted_turns

    try:
        await resume_interrupted_turns()
    except Exception:  # pragma: no cover - best-effort crash recovery
        pass

    # RAG (Phase 12): flag documents left mid-ingest by a crash; trim old query logs.
    try:
        from .rag import ingest as _rag_ingest
        from .rag.pipeline import purge_old_query_logs

        await _rag_ingest.sweep_interrupted()
        await purge_old_query_logs(settings.rag_query_log_retention_days)
    except Exception:  # pragma: no cover - best-effort
        pass

    # Orchestration (Phase 14): trim old agent_runs rows.
    try:
        from .core.orchestration.runs import purge_old_runs

        await purge_old_runs(settings.orch_run_log_retention_days)
    except Exception:  # pragma: no cover - best-effort
        pass

    from .scheduler import cron_jobs

    try:
        await cron_jobs.start()
    except Exception:  # pragma: no cover - scheduler is best-effort
        pass

    yield

    from .core.ws_manager import manager

    await manager.shutdown()
    try:
        await cron_jobs.shutdown()
    except Exception:  # pragma: no cover
        pass


app = FastAPI(title="PAGI", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATUS_CODE = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(_request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    code = _STATUS_CODE.get(exc.status_code, "error")
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": code, "message": str(detail)}}
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request failed schema validation",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )

from .api import (  # noqa: E402
    routes_admin,
    routes_agent_eval,
    routes_agents,
    routes_approvals,
    routes_auth,
    routes_chat,
    routes_conversations,
    routes_cron,
    routes_files,
    routes_harness,
    routes_mcp,
    routes_meta,
    routes_projects,
    routes_rag,
    routes_workspace,
)

for module in (
    routes_auth,
    routes_agents,
    routes_conversations,
    routes_files,
    routes_workspace,
    routes_approvals,
    routes_mcp,
    routes_cron,
    routes_rag,
    routes_meta,
    routes_agent_eval,
    routes_harness,
    routes_projects,
    routes_admin,
    routes_chat,
):
    app.include_router(module.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
