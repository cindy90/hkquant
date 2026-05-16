"""Tests for `hk_ipo_agent.common.exceptions`."""

from __future__ import annotations

import pytest

from hk_ipo_agent.common.exceptions import (
    AuthorizationError,
    HkIpoAgentException,
    InvalidStateTransition,
    LLMError,
    LLMRateLimitError,
    SnapshotImmutabilityError,
    ValidationError,
)


def test_root_exception_is_exception() -> None:
    assert issubclass(HkIpoAgentException, Exception)


def test_default_message_used_when_none_passed() -> None:
    exc = HkIpoAgentException()
    assert str(exc) == HkIpoAgentException.default_message


def test_explicit_message_overrides_default() -> None:
    exc = HkIpoAgentException("boom")
    assert str(exc) == "boom"


def test_context_attached() -> None:
    exc = HkIpoAgentException("oops", ipo_id="abc", checkpoint=30)
    assert exc.context == {"ipo_id": "abc", "checkpoint": 30}


def test_hierarchy_invalid_state_transition() -> None:
    assert issubclass(InvalidStateTransition, HkIpoAgentException)


def test_hierarchy_llm_rate_limit() -> None:
    assert issubclass(LLMRateLimitError, LLMError)
    assert issubclass(LLMRateLimitError, HkIpoAgentException)


def test_hierarchy_snapshot_immutability() -> None:
    assert issubclass(SnapshotImmutabilityError, HkIpoAgentException)


def test_api_error_has_status_and_type() -> None:
    assert AuthorizationError.http_status == 403
    assert AuthorizationError.problem_type.startswith("https://")
    assert ValidationError.http_status == 422


def test_can_raise_and_catch_as_root() -> None:
    """A consumer that catches HkIpoAgentException should see all project errors."""
    for exc_cls in (InvalidStateTransition, LLMRateLimitError, AuthorizationError):
        with pytest.raises(HkIpoAgentException):
            raise exc_cls("test")
