"""The local API (spec 13, decision 10).

The application factory takes its settings from the caller so that tests can
point it at a temporary database. Binding is the caller's job: `host` and
`port` come from settings and default to loopback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Settings
from ..db import connect, migrate
from .auth import require_token

DESCRIPTION = "Local API for TownRecord. Every request needs a bearer token."


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_path = Path(app.state.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(db_path)
        try:
            migrate(conn)
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="TownRecord local API",
        description=DESCRIPTION,
        version=settings.version,
        lifespan=lifespan,
        # Every route added to this application runs the token check first, so
        # no route is public by accident (spec 13.1, decision 10 rule 2).
        dependencies=[Depends(require_token)],
        # FastAPI's own docs routes do not pick up those dependencies, so they
        # are turned off here and added back below with the check in place.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.db_path = settings.db_path

    @app.get("/v1/health")
    def health() -> dict[str, str]:
        """Report that the service is running."""
        return {"status": "ok", "version": settings.version}

    @app.get(
        "/openapi.json",
        include_in_schema=False,
        dependencies=[Depends(require_token)],
    )
    def openapi_schema() -> JSONResponse:
        """Return the OpenAPI description of this API."""
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=[Depends(require_token)])
    def swagger_ui() -> HTMLResponse:
        """Return the API browser."""
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} docs")

    @app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_token)])
    def redoc_page() -> HTMLResponse:
        """Return the reference page."""
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} reference")

    return app
