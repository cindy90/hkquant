"""R8-1 — regime_score_from_cache + _load_market_env_cache fail loudly on
missing fixture / no snapshot, instead of silently returning 0.0.

Pre-R8-1 both paths returned 0.0 on missing data:
  * ``_load_market_env_cache`` returned ``()`` after logging a warning
    when the fixture file didn't exist.
  * ``regime_score_from_cache`` returned 0.0 when ``market_env_for``
    found no snapshot ≤ anchor.

0.0 is the SKIP gate threshold (ADR 0005 §2: ``regime >= 0`` → keep,
``regime < 0`` → SKIP). Returning 0.0 on missing data means a backtest
or live policy_agent that lost its fixture silently flips into
"all-pass" mode — exactly the silent-degradation pattern CLAUDE.md
forbids for prediction-lifecycle signals.

Post-R8-1:
  * ``_load_market_env_cache`` raises ``RuntimeError`` with an actionable
    message ("run scripts/export_market_env_cache.py") when the fixture
    is absent.
  * ``regime_score_from_cache`` raises ``RuntimeError`` when no snapshot
    precedes the anchor, surfacing the missing-data condition.
"""

from __future__ import annotations

from datetime import date

import pytest

from hk_ipo_agent.backtest.regime_detection import (
    _load_market_env_cache,
    regime_score_from_cache,
)


def test_load_market_env_cache_raises_when_fixture_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8-1 — missing fixture file must raise, not return empty tuple."""
    from hk_ipo_agent.backtest import regime_detection as mod

    # Point the fixture path at a known-nonexistent location.
    fake_path = mod._FIXTURE_PATH.parent / "__r8_1_nonexistent_fixture__.json"
    monkeypatch.setattr(mod, "_FIXTURE_PATH", fake_path)
    _load_market_env_cache.cache_clear()

    with pytest.raises(RuntimeError, match="market_environment_cache"):
        _load_market_env_cache()


def test_load_market_env_cache_error_mentions_export_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8-1 — error message points the operator at the fix."""
    from hk_ipo_agent.backtest import regime_detection as mod

    fake_path = mod._FIXTURE_PATH.parent / "__r8_1_nonexistent_fixture__.json"
    monkeypatch.setattr(mod, "_FIXTURE_PATH", fake_path)
    _load_market_env_cache.cache_clear()

    with pytest.raises(RuntimeError) as exc_info:
        _load_market_env_cache()
    msg = str(exc_info.value)
    assert "export_market_env_cache" in msg, (
        "R8-1: error must mention the export script so operators know how to regenerate the fixture"
    )


def test_regime_score_from_cache_raises_when_no_snapshot_precedes_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8-1 — when no snapshot ≤ anchor exists, raise instead of returning 0.0.

    Returning 0.0 silently flips the regime gate into "all-pass" mode
    because the gate threshold IS 0.0. A backtest with a stale fixture
    that doesn't reach back to the anchor date would emit fake-positive
    pass signals everywhere — the opposite of fail-fast.
    """
    from hk_ipo_agent.backtest import regime_detection as mod

    # Make ``market_env_for`` return None for any anchor.
    monkeypatch.setattr(mod, "market_env_for", lambda anchor: None)

    with pytest.raises(RuntimeError, match="no market environment snapshot"):
        regime_score_from_cache(date(2026, 1, 1))


def test_regime_score_from_cache_returns_value_when_snapshot_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8-1 — happy path unchanged: real snapshot → real regime score."""
    from hk_ipo_agent.backtest import regime_detection as mod

    class _Env:
        hk_ipo_30d_avg_d30 = 0.075

    monkeypatch.setattr(mod, "market_env_for", lambda anchor: _Env())
    assert regime_score_from_cache(date(2026, 1, 1)) == pytest.approx(0.075)


def test_load_market_env_cache_succeeds_when_fixture_present() -> None:
    """R8-1 — happy path: real fixture file → loads + returns snapshots.

    Doesn't assert any specific row count; just that the function returns
    without raising and gives back a tuple of snapshots when the real
    fixture is on disk (CI + dev environments both have it after Phase 2).
    """
    _load_market_env_cache.cache_clear()
    try:
        snapshots = _load_market_env_cache()
    except RuntimeError:
        pytest.skip("fixture missing in this environment — covered by the raises test instead")
    assert isinstance(snapshots, tuple)
