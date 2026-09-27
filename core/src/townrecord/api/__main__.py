"""Run the local API: `uv run python -m townrecord.api`."""

from __future__ import annotations

import uvicorn

from ..config import Settings
from .app import create_app


def main() -> None:
    """Start the server on the configured host and port."""
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
