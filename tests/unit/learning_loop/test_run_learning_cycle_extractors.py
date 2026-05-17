"""R3-2 — JSONB extractors in scripts/run_learning_cycle.py.

Pre-fix _load_outcome_samples hard-coded predicted_median_price /
realized_price_at_60d / bear_flagged_risk / agent_scores /
agent_realized_hits to None / {} so 3 of 4 drift sub-detectors
(valuation_bias, bear_miss_rate, agent_calibration_drift) silently
never fired. The learning report's "no signals fired" was a false
negative, not a sign of model stability.

These tests pin the extractors so future refactors can't regress to
all-None.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

# scripts/run_learning_cycle.py isn't a package — import via path injection.
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_learning_cycle as rlc

# ---------------------------------------------------------------------------
# _extract_predicted_price
# ---------------------------------------------------------------------------


def test_extract_predicted_price_uses_fair_when_present() -> None:
    assert rlc._extract_predicted_price({"price_range_fair": "12.5"}) == 12.5


def test_extract_predicted_price_falls_back_to_midpoint() -> None:
    """When fair is absent, the midpoint of low/high is used."""
    out = rlc._extract_predicted_price({"price_range_low": "10", "price_range_high": "20"})
    assert out == 15.0


def test_extract_predicted_price_returns_none_when_empty() -> None:
    assert rlc._extract_predicted_price(None) is None
    assert rlc._extract_predicted_price({}) is None


def test_extract_predicted_price_tolerates_malformed_values() -> None:
    """Malformed numeric strings → None (not crash)."""
    assert rlc._extract_predicted_price({"price_range_fair": "not-a-number"}) is None


# ---------------------------------------------------------------------------
# _extract_bear_flagged_risk
# ---------------------------------------------------------------------------


def test_extract_bear_flagged_risk_detects_chinese_negative_keywords() -> None:
    debate = {"rounds": [{"bear_argument": "估值偏高，存在显著下行风险"}]}
    assert rlc._extract_bear_flagged_risk(debate) is True


def test_extract_bear_flagged_risk_detects_english_negative_keywords() -> None:
    debate = {"rounds": [{"bear_argument": "Significant downside risk on valuation"}]}
    assert rlc._extract_bear_flagged_risk(debate) is True


def test_extract_bear_flagged_risk_returns_false_when_bear_silent() -> None:
    debate = {"rounds": [{"bear_argument": "Concerns acknowledged, but resolved."}]}
    # "concerns" IS a negative keyword — should be True.
    assert rlc._extract_bear_flagged_risk(debate) is True


def test_extract_bear_flagged_risk_returns_false_for_neutral_bear() -> None:
    debate = {"rounds": [{"bear_argument": "Strong fundamentals + clear catalysts."}]}
    assert rlc._extract_bear_flagged_risk(debate) is False


def test_extract_bear_flagged_risk_returns_none_when_no_debate() -> None:
    assert rlc._extract_bear_flagged_risk(None) is None
    assert rlc._extract_bear_flagged_risk({}) is None


# ---------------------------------------------------------------------------
# _extract_agent_scores
# ---------------------------------------------------------------------------


def test_extract_agent_scores_collects_overall_scores() -> None:
    agents = {
        "fundamental": {"overall_score": 75.0, "scores": {"x": 1}},
        "sentiment": {"overall_score": 60.5},
    }
    out = rlc._extract_agent_scores(agents)
    assert out == {"fundamental": 75.0, "sentiment": 60.5}


def test_extract_agent_scores_skips_malformed_entries() -> None:
    """Non-dict payloads + missing score keys are dropped silently."""
    agents = {
        "fundamental": {"overall_score": 80},
        "broken": "not a dict",
        "no_score": {"other_field": 1},
    }
    out = rlc._extract_agent_scores(agents)
    assert set(out.keys()) == {"fundamental"}


def test_extract_agent_scores_empty_input_returns_empty() -> None:
    assert rlc._extract_agent_scores(None) == {}
    assert rlc._extract_agent_scores({}) == {}


# ---------------------------------------------------------------------------
# _extract_agent_realized_hits
# ---------------------------------------------------------------------------


def test_extract_agent_realized_hits_only_high_confidence_agents() -> None:
    """Only agents with overall_score ≥ 70 are tagged with the decision hit."""
    agents = {
        "high_conf": {"overall_score": 85},
        "medium_conf": {"overall_score": 50},
        "high_conf_2": {"overall_score": 72},
    }
    out = rlc._extract_agent_realized_hits(agents, decision_correct=True)
    assert set(out.keys()) == {"high_conf", "high_conf_2"}
    assert all(v is True for v in out.values())


def test_extract_agent_realized_hits_propagates_wrong_decision() -> None:
    """If the decision was wrong, every high-conf agent is marked as a miss."""
    agents = {"a": {"overall_score": 75}}
    out = rlc._extract_agent_realized_hits(agents, decision_correct=False)
    assert out == {"a": False}


def test_extract_agent_realized_hits_decision_unknown_returns_empty() -> None:
    """If decision_correct is None, we can't attribute → empty dict."""
    agents = {"a": {"overall_score": 80}}
    out = rlc._extract_agent_realized_hits(agents, decision_correct=None)
    assert out == {}


# ---------------------------------------------------------------------------
# _extract_realized_price
# ---------------------------------------------------------------------------


def test_extract_realized_price_returns_none_when_no_actual_price() -> None:
    """Phase 7.5b outcome rows don't carry absolute price by default
    (only cumulative return); the extractor must drop to None so the
    valuation_bias slice for this sample is simply skipped, not silently
    misreported."""
    outcome = SimpleNamespace()
    assert rlc._extract_realized_price(outcome) is None


def test_extract_realized_price_reads_actual_price_extension() -> None:
    outcome = SimpleNamespace(actual_price="42.0")
    assert rlc._extract_realized_price(outcome) == 42.0


def test_extract_realized_price_tolerates_malformed_value() -> None:
    outcome = SimpleNamespace(actual_price="N/A")
    assert rlc._extract_realized_price(outcome) is None
