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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import Settings
from ..db import connect, migrate
from . import docs_assets, read
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
    # The artifact rows hold a path relative to a root the user chose, so the
    # root is runtime state and the read routes take it from here (spec 8.6).
    app.state.storage_root = settings.storage_root

    app.include_router(read.router)

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

    @app.get(
        f"{docs_assets.PREFIX}/{{name}}",
        include_in_schema=False,
        dependencies=[Depends(require_token)],
    )
    def docs_asset(name: str) -> FileResponse:
        """Return one file the API browser is made of, from this machine.

        It is a route and not a mount, so the request runs the token check like
        every other one (spec 13.1, decision 10 rule 2). Only the names the page
        loads are served, and each is served from the package's own directory.
        """
        if not docs_assets.is_served(name):
            raise read.not_found(f"There is no docs asset named {name!r}.")
        path = docs_assets.asset_path(name)
        if path is None:
            raise read.not_found(
                f"The docs asset {name!r} is missing from the installed "
                f"{docs_assets.PACKAGE} package."
            )
        return FileResponse(path, media_type=docs_assets.ASSETS[name])

    @app.get("/docs", include_in_schema=False, dependencies=[Depends(require_token)])
    def swagger_ui() -> HTMLResponse:
        """Return the API browser, with its files served from this machine."""
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} docs",
            swagger_js_url=docs_assets.SWAGGER_JS,
            swagger_css_url=docs_assets.SWAGGER_CSS,
            swagger_favicon_url=docs_assets.FAVICON,
        )

    @app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_token)])
    def redoc_page() -> HTMLResponse:
        """Return the reference page, with its file served from this machine."""
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} reference",
            redoc_js_url=docs_assets.REDOC_JS,
            redoc_favicon_url=docs_assets.FAVICON,
            with_google_fonts=False,
        )

    return app
