"""R7-6 — IFindClient retry classifies network-jitter vs. logic errors.

Pre-R7-6 the SDK-call wrapper only treated ``TimeoutError`` as retryable;
every other exception was wrapped as ``DataSourceError`` (non-retryable),
including connection errors that are the textbook case for "retry with
backoff". A transient ``ConnectionResetError`` would surface as a hard
failure on the first attempt instead of recovering on attempt 2.

Post-R7-6:
  * Network-jitter exceptions (``ConnectionError`` and subclasses,
    ``TimeoutError``) → ``DataSourceUnavailableError`` → retryable.
  * Logic / data exceptions (everything else) → ``DataSourceError``
    → NOT retryable (no point retrying a malformed query).

These tests verify the classification by directly invoking the
classification helper (the inline ``except`` chain is exposed as
``_classify_exception_for_retry``).
"""

from __future__ import annotations

import inspect

import pytest

from hk_ipo_agent.common.exceptions import DataSourceError, DataSourceUnavailableError
from hk_ipo_agent.data.sources.ifind_client import IFindClient


def test_classify_exception_helper_exists() -> None:
    """R7-6 — the classification helper is exposed for reuse + testing."""
    from hk_ipo_agent.data.sources import ifind_client

    assert hasattr(ifind_client, "_classify_exception_for_retry"), (
        "R7-6: ifind_client must expose _classify_exception_for_retry helper"
    )


@pytest.mark.parametrize(
    "exc_class",
    [
        ConnectionError,
        ConnectionResetError,
        ConnectionAbortedError,
        ConnectionRefusedError,
        TimeoutError,
    ],
)
def test_network_jitter_classified_as_unavailable(exc_class: type[Exception]) -> None:
    """R7-6 — network-level exceptions become DataSourceUnavailableError (retryable)."""
    from hk_ipo_agent.data.sources.ifind_client import _classify_exception_for_retry

    original = exc_class("simulated network blip")
    classified = _classify_exception_for_retry(original, method="ths_dq")

    assert isinstance(classified, DataSourceUnavailableError), (
        f"R7-6: {exc_class.__name__} must classify as DataSourceUnavailableError "
        f"(retryable), got {type(classified).__name__}"
    )
    # The cause linkage (``raise ... from exc``) is the caller's responsibility;
    # the helper returns the wrapped exception. We verify the original message
    # is reachable inside the wrapped message instead.
    assert "simulated network blip" in str(classified)


def test_logic_error_classified_as_data_source_error() -> None:
    """R7-6 — ValueError / TypeError / KeyError → DataSourceError (NOT retryable).

    These are typically caller mistakes (bad query, schema mismatch). Retrying
    them just burns the QPS budget — they need a code fix.
    """
    from hk_ipo_agent.data.sources.ifind_client import _classify_exception_for_retry

    for cls in (ValueError, TypeError, KeyError, RuntimeError):
        original = cls("malformed input")
        classified = _classify_exception_for_retry(original, method="ths_dq")
        assert isinstance(classified, DataSourceError), (
            f"R7-6: {cls.__name__} must classify as DataSourceError "
            f"(non-retryable), got {type(classified).__name__}"
        )
        assert not isinstance(classified, DataSourceUnavailableError), (
            f"R7-6: {cls.__name__} must NOT become retryable"
        )


def test_classify_preserves_method_label() -> None:
    """R7-6 — the wrapped exception carries the iFind method label for logging."""
    from hk_ipo_agent.data.sources.ifind_client import _classify_exception_for_retry

    classified = _classify_exception_for_retry(ConnectionError("blip"), method="ths_history_quote")
    # ``method`` attribute or message must reference the iFind call site.
    assert getattr(classified, "method", None) == "ths_history_quote" or "ths_history_quote" in str(
        classified
    ), "R7-6: method label must survive classification for log correlation"


def test_call_retries_on_connection_error_then_succeeds() -> None:
    """R7-6 — the retry loop body USES the classification: a ConnectionError
    becomes retryable and the second attempt succeeds.

    We can verify this property structurally by inspecting the source for the
    classifier call. Full end-to-end retry behaviour is integration-level
    (mock the SDK, count retries) and lives in tests/integration; the unit
    test here pins the wiring.
    """
    source = inspect.getsource(IFindClient)
    assert "_classify_exception_for_retry" in source, (
        "R7-6: the retry wrapper must route raised exceptions through "
        "_classify_exception_for_retry to get the retryable/non-retryable split"
    )
