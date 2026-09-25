"""FastAPI application entrypoint.

Run locally with::

    cd api && uvicorn main:app --reload

Or from the repo root::

    uvicorn api.main:app --reload

Both forms are supported because the ``Dockerfile.api`` layer copies the
``api`` package into ``/app`` and uses the latter invocation.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.responses import FileResponse, JSONResponse

from api import database, hosted_mcp, mcp_oauth
from api.auth import get_current_user
from api.config import get_settings
from api.database import init_db
from api.events import get_broadcaster
from api.observability import (
    ObservabilityMiddleware,
    configure_runtime,
    flush_telemetry,
    log_event,
    metrics_response,
    metrics_token_matches,
)
from api.routes import (
    agent,
    apikeys,
    auth,
    comments,
    events,
    knowledge,
    projects,
    proposals,
    sessions,
    subprojects,
    tickets,
    workspaces,
)
from api.security import (
    RequestBodyLimitMiddleware,
    SecurityMiddleware,
    parse_bearer_token,
)
from api.version import __version__, git_sha


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    broadcaster = get_broadcaster()
    await broadcaster.start(
        get_settings().effective_realtime_database_url()
    )
    try:
        app.state.mcp_manager = hosted_mcp.make_manager()
        async with app.state.mcp_manager.run():
            yield
    finally:
        await broadcaster.stop()
        flush_telemetry()


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_production()
    configure_runtime(settings)
    app = FastAPI(
        title="Mouvadah API",
        version=__version__,
        lifespan=lifespan,
    )

    # Build CORS origins from config + frontend_url.
    cors_origins = list(settings.cors_origins)
    if settings.frontend_url not in cors_origins:
        cors_origins.append(settings.frontend_url)
    # Filter out wildcard origins — incompatible with allow_credentials=True.
    cors_origins = [o for o in cors_origins if o != "*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "Content-Disposition",
            "X-Mouvadah-Export-SHA256",
            "X-Request-ID",
        ],
    )
    # Added after CORS so it wraps every API/static response, including CORS
    # rejections and mounted sub-application routes.
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware)
    # Added last so it wraps security/CORS/static responses and correlates
    # failures that occur before route authentication.
    app.add_middleware(ObservabilityMiddleware)

    # --- API v1 ---
    api_v1 = FastAPI(title="Mouvadah API v1", version=__version__)

    # Public routes (no auth).
    api_v1.include_router(auth.router)

    # UI-facing routes (require authenticated user).
    ui_auth = [Depends(get_current_user)]
    api_v1.include_router(projects.router, dependencies=ui_auth)
    api_v1.include_router(subprojects.router, dependencies=ui_auth)
    api_v1.include_router(tickets.router, dependencies=ui_auth)
    api_v1.include_router(comments.router, dependencies=ui_auth)
    api_v1.include_router(knowledge.router, dependencies=ui_auth)
    api_v1.include_router(proposals.router, dependencies=ui_auth)
    api_v1.include_router(sessions.router, dependencies=ui_auth)
    # The stream performs its own function-scoped authentication so a
    # request-scoped SQLAlchemy session is not retained for the connection.
    api_v1.include_router(events.router)
    api_v1.include_router(apikeys.router, dependencies=ui_auth)
    api_v1.include_router(workspaces.router, dependencies=ui_auth)

    # Agent routes (also require authenticated user via session or API key).
    api_v1.include_router(agent.router, dependencies=ui_auth)

    app.mount("/api/v1", api_v1)
    app.api_v1 = api_v1  # exposed for test dependency overrides

    # --- Health check ---
    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str | None]:
        return {
            "status": "ok",
            "version": __version__,
            "git_sha": git_sha(),
            "realtime": get_broadcaster().status(),
        }

    @app.get("/readyz", tags=["meta"])
    def readyz():
        realtime = get_broadcaster().status()
        try:
            with database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            log_event(
                logging.getLogger("mouvadah.readiness"),
                logging.ERROR,
                "readiness.database.failed",
                exc_info=True,
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "database": "unavailable",
                    "realtime": realtime,
                    "version": __version__,
                    "git_sha": git_sha(),
                },
            )
        if realtime in {"degraded", "not_started"}:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "database": "healthy",
                    "realtime": realtime,
                    "version": __version__,
                    "git_sha": git_sha(),
                },
            )
        return {
            "status": "ready",
            "database": "healthy",
            "realtime": realtime,
            "version": __version__,
            "git_sha": git_sha(),
        }

    @app.get("/internal/metrics", include_in_schema=False)
    def internal_metrics(request: Request):
        configured_token = settings.metrics_bearer_token_value()
        if configured_token is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        supplied_token = parse_bearer_token(
            request.headers.get("Authorization", "")
        )
        if not metrics_token_matches(configured_token, supplied_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return metrics_response()

    app.include_router(mcp_oauth.router)
    hosted_mcp.install(app)

    # --- Serve built frontend (production) ---
    dist_dir = Path("web/dist")
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def spa_catch_all(full_path: str):
            """SPA fallback: serve index.html for any non-API path."""
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(str(dist_dir / "index.html"))

    return app


app = create_app()
