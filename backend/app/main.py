"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import session as db

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    info = await db.ping()
    state = await db.schema_state()
    log.info("startup", **info, **state)
    if state["tables"] == 0:
        log.error("schema_missing", hint="run backend/db/schema.sql")
    else:
        # After the schema exists, so a first boot on an empty database still
        # ends with a usable login. Failure here must not stop the app: an
        # unreachable auth table is worth surfacing as a 401 with a log line,
        # not as a container that will not start.
        from app.auth.users import bootstrap_superuser
        try:
            await bootstrap_superuser()
        except Exception:
            log.exception("auth_bootstrap_failed")

    # A wildcard origin and credentialed requests are mutually exclusive: the
    # browser refuses to send the session cookie and every call 401s with
    # nothing in the server log to explain it.
    if "*" in settings.cors_origin_list:
        log.error("cors_wildcard_breaks_auth",
                  hint="CORS_ORIGINS=* cannot be used with cookie sessions; "
                       "list the UI's origin explicitly")
    yield
    await db.engine.dispose()


app = FastAPI(
    title="Salesforce Org Cleanup Analyzer",
    version="0.1.0",
    lifespan=lifespan,
)

from app.api.auth import router as auth_router   # noqa: E402
from app.api.routes import router as api_router  # noqa: E402
from app.api.chat import router as chat_router   # noqa: E402
from app.api.runs import router as runs_router   # noqa: E402
from app.auth.deps import current_user           # noqa: E402

# Auth first, and deliberately unguarded: /login and /me must be reachable
# while signed out. Each mutating route in it carries its own guard.
app.include_router(auth_router)

# Everything else requires a session. Applying the dependency here rather than
# per endpoint means a route added later is protected by default; the reverse
# arrangement fails open, and does it silently.
_guard = [Depends(current_user)]
app.include_router(api_router, dependencies=_guard)
app.include_router(runs_router, dependencies=_guard)
app.include_router(chat_router, dependencies=_guard)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The browser needs Last-Event-ID visible to resume an interrupted stream.
    expose_headers=["Last-Event-ID"],
)


@app.get("/health")
async def health() -> dict:
    """Reports whether the schema is applied, not merely whether Postgres answers.

    A probe that only checks connectivity reports green against an empty
    database - precisely the failure worth catching.
    """
    conn = await db.ping()
    state = await db.schema_state()
    ok = state["tables"] > 0 and state["append_event_fn"]
    return {
        "status": "ok" if ok else "degraded",
        "database": conn,
        "schema": state,
        "org": settings.sf_instance_url,
        "api_version": settings.sf_api_version,
        "llm": {
            "enabled": settings.llm_enabled,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
        },
    }
