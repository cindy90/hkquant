"""RFC 7807 Problem Details error handler per PROJECT_SPEC.md §16.8 + CLAUDE.md.

Translates uncaught exceptions into ``APIError`` JSON bodies. Pydantic
validation errors get a 422 with the field-level breakdown.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...common.schemas import APIError


def _problem(
    *,
    status: int,
    title: str,
    detail: str,
    type_: str = "about:blank",
    request_id: str | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body = APIError(
        type=type_,
        title=title,
        status=status,
        detail=detail,
        request_id=request_id,
        validation_errors=validation_errors,
    )
    headers = {"Content-Type": "application/problem+json"}
    if request_id:
        headers["X-Request-Id"] = request_id
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach all custom error handlers to ``app``."""

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return _problem(
            status=exc.status_code,
            title=exc.detail if isinstance(exc.detail, str) else "HTTP Error",
            detail=str(exc.detail),
            request_id=rid,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return _problem(
            status=422,
            title="Validation Error",
            detail="Request payload failed schema validation",
            type_="https://example.com/errors/validation",
            request_id=rid,
            validation_errors=[
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        )

    @app.exception_handler(ValidationError)
    async def _pydantic_exc(request: Request, exc: ValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return _problem(
            status=422,
            title="Pydantic Validation Error",
            detail=str(exc),
            request_id=rid,
        )

    @app.exception_handler(Exception)
    async def _fallback_exc(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return _problem(
            status=500,
            title="Internal Server Error",
            detail=type(exc).__name__,
            request_id=rid,
        )


__all__ = ("register_error_handlers",)
