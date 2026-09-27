"""Local API: the FastAPI application and its bearer token rules."""

from .app import create_app

__all__ = ["create_app"]
