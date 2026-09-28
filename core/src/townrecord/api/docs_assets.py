"""The files the API browser is made of, served from this machine (spec 17).

The API browser draws Swagger UI and ReDoc, and both are normally loaded from a
content delivery network. A machine with no network would show an empty page,
so the files come from ``fastapi-offline``, the package pinned in
``pyproject.toml``, and are served by a route of this application instead.

The route is where the token check lives (decision 10 rule 2). The package's
own helper mounts its files with ``StaticFiles``, and a mount is not a route:
it would not run the check, so the assets would be public on any address the
service is bound to. Nothing here mounts anything.

Only the names below are served, and each is served whole from the package
directory. A name that is not in the table answers 404 rather than being joined
onto a path, so no request can walk out of the directory.

Provenance, for the pinned version 1.7.7: the two JavaScript files and the
stylesheet are the Swagger UI and ReDoc distributions (Apache-2.0 and MIT, as
the package's own metadata states), and the icon is the FastAPI favicon. The
SHA-256 of each file as the pin is expected to deliver it is in ``SHA256``,
and a test reads this table and hashes what the route served, so a build of the
package that ships different bytes is caught rather than trusted.
"""

from __future__ import annotations

from pathlib import Path

import fastapi_offline

#: The package the files come from, named so a plain sentence can say it.
PACKAGE = "fastapi-offline"

#: Where the pinned package keeps its copy of the files.
ASSET_DIR = Path(fastapi_offline.__file__).parent / "static"

#: The files that are served, by name, with the media type each is sent as.
ASSETS: dict[str, str] = {
    "swagger-ui-bundle.js": "text/javascript; charset=utf-8",
    "swagger-ui.css": "text/css; charset=utf-8",
    "redoc.standalone.js": "text/javascript; charset=utf-8",
    "favicon.png": "image/png",
}

#: The SHA-256 of each file at this pin. A test checks the served bytes.
SHA256: dict[str, str] = {
    "swagger-ui-bundle.js": "4b5a9b35a1adf37f00d39c0bae23cdb37007e2946240ef035137cc85b4d55349",
    "swagger-ui.css": "ca238f7d7c2cf4480c1e77a9c3b9da915ab216e96ffd354e69076560c650c6de",
    "redoc.standalone.js": "1320f442151c57c447d3b70c7ffc6c4f86d08464020fe34c8cc5d3164e9944f0",
    "favicon.png": "d16f72cf5a2773eba499cc7f3db1923c6f5f73de990f739e3a05806aafec15b7",
}

#: The path the assets are served under. It is a route of this application and
#: not a mount, so the HTML and the route cannot drift apart.
PREFIX = "/docs-assets"

#: The four paths the two pages load, written once and used by the pages.
SWAGGER_JS = f"{PREFIX}/swagger-ui-bundle.js"
SWAGGER_CSS = f"{PREFIX}/swagger-ui.css"
REDOC_JS = f"{PREFIX}/redoc.standalone.js"
FAVICON = f"{PREFIX}/favicon.png"


def asset_path(name: str) -> Path | None:
    """Return the file for one asset name, or None when there is no such asset.

    None covers both a name that is not served and a file the installed package
    does not have. The caller says which of the two it was, because a name that
    was never served and a file that went missing are different facts.
    """
    if name not in ASSETS:
        return None
    path = ASSET_DIR / name
    return path if path.is_file() else None


def is_served(name: str) -> bool:
    """Say whether this name is one of the files that are served."""
    return name in ASSETS
