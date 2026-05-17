"""OpenAPI 3.1 customization per PROJECT_SPEC.md §16 + CLAUDE.md UI 集成约束.

CLAUDE.md HARD constraint: ``openapi-typescript`` on the UI side must
generate a complete + stable TypeScript surface. We inject:

- Application info (title / version / description)
- Bearer-token security scheme
- Server URL from ``Settings.api.host:port``
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from ..common.settings import get_settings


def build_custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Generate the augmented OpenAPI 3.1 schema."""
    if app.openapi_schema:
        return app.openapi_schema

    settings = get_settings()
    schema = get_openapi(
        title="HK IPO Cornerstone Agent API",
        version=settings.orchestrator.system_version,
        openapi_version="3.1.0",
        description=(
            "REST + SSE + WebSocket API for the HK IPO cornerstone investment "
            "decision system. See PROJECT_SPEC.md §16 + PROJECT_SPEC_UI.md."
        ),
        routes=app.routes,
    )

    # Inject Bearer auth scheme.
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }

    schema["security"] = [{"BearerAuth": []}]

    # Server URL (best-effort).
    schema["servers"] = [{"url": f"http://{settings.api.host}:{settings.api.port}"}]

    app.openapi_schema = schema
    return schema


def install_openapi(app: FastAPI) -> None:
    """Bind the custom builder to ``app.openapi``."""
    app.openapi = lambda: build_custom_openapi(app)  # type: ignore[method-assign]


__all__ = ("build_custom_openapi", "install_openapi")
