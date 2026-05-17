"""structlog JSON logging per PROJECT_SPEC.md §10.4.

Outputs structured JSON to stdout (Prometheus / Loki friendly).
Every log record carries `ipo_id` / `agent_role` / `cost_usd` context where
available; secrets are redacted by a global processor.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

if TYPE_CHECKING:
    from types import TracebackType

# Field names that MUST be redacted before serialization (case-insensitive substring match).
_REDACT_KEYS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "client_secret",
    "anthropic_api_key",
    "kimi_api_key",
    "llama_cloud_api_key",
)


def _redact(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Replace any value whose key matches a redact-key with ``***REDACTED***``."""
    for key in list(event_dict):
        lowered = key.lower()
        if any(needle in lowered for needle in _REDACT_KEYS):
            event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging(*, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog + stdlib logging globally.

    Idempotent — safe to call multiple times.

    Args:
        level: log level name (DEBUG / INFO / WARNING / ERROR / CRITICAL).
        json:  if True, emit JSON (production); else colored console (dev).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact,
    ]

    if json:
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a project logger, optionally namespaced by `name`.

    Return type is ``Any`` because structlog's BoundLogger generic is hard to
    pin down across processor chains; callers should treat it as a structured
    logger with ``info`` / ``debug`` / ``warning`` / ``error`` / ``bind``.
    """
    return structlog.get_logger(name)


class LogContext:
    """Context manager that binds structlog context vars (auto-cleared on exit).

    Example:
        with LogContext(ipo_id=str(snap.ipo_id), agent_role="fundamental"):
            log.info("agent_started")
            ...
    """

    def __init__(self, **context: Any) -> None:
        self._context = context
        self._snapshot: dict[str, Any] | None = None

    def __enter__(self) -> LogContext:
        self._snapshot = dict(structlog.contextvars.get_contextvars())
        bind_contextvars(**self._context)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        clear_contextvars()
        if self._snapshot is not None:
            bind_contextvars(**self._snapshot)


__all__ = (
    "LogContext",
    "configure_logging",
    "get_logger",
)
