"""CORS configuration helper per PROJECT_SPEC.md §16 + CLAUDE.md v1.2.1.

CLAUDE.md hard constraint: production must NOT allow ``*`` origin. We read
the whitelist from ``Settings.api.cors_origins`` and pass it to
Starlette's ``CORSMiddleware``.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from ...common.settings import get_settings


def install_cors(app: FastAPI) -> None:
    """Attach Starlette's ``CORSMiddleware`` with the configured whitelist."""
    settings = get_settings()
    origins = list(settings.api.cors_origins or [])
    if "*" in origins and settings.environment.lower() == "prod":
        raise RuntimeError(
            "CORS origin '*' is not allowed in production (CLAUDE.md v1.2.1)"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )


__all__ = ("install_cors",)
