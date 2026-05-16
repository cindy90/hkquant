"""Tests for `hk_ipo_agent.common.logging`."""

from __future__ import annotations

import json

import structlog

from hk_ipo_agent.common.logging import LogContext, _redact, configure_logging, get_logger


def test_redact_processor_redacts_known_keys() -> None:
    event = {
        "anthropic_api_key": "sk-ant-...",
        "password": "topsecret",
        "user_email": "x@y.com",
        "ipo_id": "12345",
    }
    out = _redact(None, "info", dict(event))
    assert out["anthropic_api_key"] == "***REDACTED***"
    assert out["password"] == "***REDACTED***"
    # Non-sensitive fields pass through
    assert out["user_email"] == "x@y.com"
    assert out["ipo_id"] == "12345"


def test_configure_logging_emits_json(capsys: object) -> None:
    """Smoke test: configure JSON logging and ensure it produces parsable JSON output."""
    configure_logging(level="DEBUG", json=True)
    log = get_logger("test.module")
    log.info("hello", x=1)
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    # At least one line is JSON
    last_line = out.strip().splitlines()[-1]
    parsed = json.loads(last_line)
    assert parsed["event"] == "hello"
    assert parsed["x"] == 1


def test_log_context_binds_and_clears() -> None:
    configure_logging(level="DEBUG", json=True)
    # Initially no bound context
    assert structlog.contextvars.get_contextvars() == {}
    with LogContext(ipo_id="abc", agent_role="fundamental"):
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["ipo_id"] == "abc"
        assert ctx["agent_role"] == "fundamental"
    # After exit context vars cleared (back to no project keys)
    after = structlog.contextvars.get_contextvars()
    assert "ipo_id" not in after
    assert "agent_role" not in after


def test_log_context_restores_outer_snapshot() -> None:
    configure_logging(level="DEBUG", json=True)
    structlog.contextvars.bind_contextvars(outer="X")
    try:
        with LogContext(inner="Y"):
            ctx = structlog.contextvars.get_contextvars()
            # NOTE: clear_contextvars in __exit__ resets to outer snapshot
            assert ctx["inner"] == "Y"
        after = structlog.contextvars.get_contextvars()
        assert after.get("outer") == "X"
        assert "inner" not in after
    finally:
        structlog.contextvars.clear_contextvars()
