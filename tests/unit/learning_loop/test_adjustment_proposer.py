"""adjustment_proposer.py tests — Phase 10b per ADR 0015."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from hk_ipo_agent.common.enums import (
    AdjustmentType,
    AlertLevel,
    Confidence,
    DriftSignalType,
)
from hk_ipo_agent.common.schemas import DriftSignal
from hk_ipo_agent.learning_loop.adjustment_proposer import (
    AdjustmentProposer,
    ProposerConfig,
)
from hk_ipo_agent.learning_loop.attribution_aggregator import AggregatedFinding
from hk_ipo_agent.learning_loop.counterfactual import (
    CounterfactualReport,
    IfBearReport,
    IfSingleModelReport,
)


def _drift(
    signal_type: DriftSignalType,
    *,
    sample_count: int = 30,
    severity: AlertLevel = AlertLevel.WARNING,
    affected: dict[str, str] | None = None,
) -> DriftSignal:
    return DriftSignal(
        detection_time=datetime.now(UTC),
        signal_type=signal_type,
        severity=severity,
        affected_dimensions=affected or {},
        metric_value=0.5,
        threshold=0.2,
        sample_count=sample_count,
        evidence="test evidence",
        related_snapshot_ids=[],
    )


def _finding(severity: str = "warning", slice_dim: str = "listing_type") -> AggregatedFinding:
    return AggregatedFinding(
        attribution_key=f"{slice_dim}=X|test_attribution",
        primary_attribution="test_attribution",
        slice_dimension=slice_dim,
        slice_value="MB-TECH" if slice_dim == "listing_type" else "fundamental",
        occurrences=5,
        share=0.5,
        related_review_ids=(uuid.uuid4(),),
        related_snapshot_ids=(uuid.uuid4(),),
        severity=severity,
    )


def _cf(*, bear_advantage: float = 0.0, ensemble_advantage: float = 0.0) -> CounterfactualReport:
    return CounterfactualReport(
        if_bear=IfBearReport(
            n_total=10,
            n_bull_won=10,
            n_bull_won_bad=5,
            bull_won_bad_rate=0.5,
            n_bear_would_have_avoided=int(5 * bear_advantage),
            bear_advantage=bear_advantage,
        ),
        if_single_model=IfSingleModelReport(
            n_samples=10,
            ensemble_hit_rate=0.5 - max(0, ensemble_advantage * -1),
            model_hit_rates={"dcf": 0.5 + max(0, ensemble_advantage * -1)},
            best_single_model="dcf",
            best_single_hit_rate=0.5 + max(0, ensemble_advantage * -1),
            ensemble_advantage=ensemble_advantage,
        ),
        summary="test",
    )


# ---------------------------------------------------------------------------
# DriftSignal → ProposedAdjustment
# ---------------------------------------------------------------------------


def test_proposer_empty_inputs_returns_empty() -> None:
    proposer = AdjustmentProposer()
    assert proposer.propose(drift_signals=[]) == []


def test_proposer_skips_signal_below_min_sample_count() -> None:
    proposer = AdjustmentProposer(ProposerConfig(min_sample_count=20))
    signal = _drift(DriftSignalType.ACCURACY_DROP, sample_count=5)
    assert proposer.propose(drift_signals=[signal]) == []


def test_proposer_maps_accuracy_drop_to_logic_change() -> None:
    proposer = AdjustmentProposer()
    signal = _drift(DriftSignalType.ACCURACY_DROP)
    proposals = proposer.propose(drift_signals=[signal])
    assert len(proposals) == 1
    assert proposals[0].adjustment_type == AdjustmentType.LOGIC_CHANGE
    assert "synthesizer" in proposals[0].target_path


def test_proposer_maps_valuation_bias_to_weight_change() -> None:
    proposer = AdjustmentProposer()
    signal = _drift(
        DriftSignalType.VALUATION_BIAS,
        affected={"listing_type": "MB-TECH"},
    )
    proposals = proposer.propose(drift_signals=[signal])
    assert proposals[0].adjustment_type == AdjustmentType.WEIGHT_CHANGE
    assert "valuation_weights" in proposals[0].target_path


def test_proposer_maps_bear_miss_to_prompt_edit() -> None:
    proposer = AdjustmentProposer()
    signal = _drift(DriftSignalType.BEAR_MISS_RATE_HIGH)
    proposals = proposer.propose(drift_signals=[signal])
    assert proposals[0].adjustment_type == AdjustmentType.PROMPT_EDIT
    assert "critic_bear" in proposals[0].target_path


def test_proposer_substitutes_agent_role_into_template_target() -> None:
    proposer = AdjustmentProposer()
    signal = _drift(
        DriftSignalType.AGENT_CALIBRATION_DRIFT,
        affected={"agent_role": "fundamental"},
    )
    proposals = proposer.propose(drift_signals=[signal])
    assert "fundamental" in proposals[0].target_path
    assert "{" not in proposals[0].target_path


def test_proposer_confidence_escalates_on_critical_severity() -> None:
    proposer = AdjustmentProposer()
    crit_signal = _drift(DriftSignalType.ACCURACY_DROP, severity=AlertLevel.CRITICAL)
    warn_signal = _drift(DriftSignalType.ACCURACY_DROP, severity=AlertLevel.WARNING)
    proposals_crit = proposer.propose(drift_signals=[crit_signal])
    proposals_warn = proposer.propose(drift_signals=[warn_signal])
    assert proposals_crit[0].confidence == Confidence.MEDIUM
    assert proposals_warn[0].confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# AggregatedFinding → ProposedAdjustment
# ---------------------------------------------------------------------------


def test_proposer_skips_info_findings() -> None:
    proposer = AdjustmentProposer()
    finding = _finding(severity="info")
    assert proposer.propose(drift_signals=[], findings=[finding]) == []


def test_proposer_critical_finding_high_confidence() -> None:
    proposer = AdjustmentProposer()
    finding = _finding(severity="critical")
    proposals = proposer.propose(drift_signals=[], findings=[finding])
    assert proposals[0].confidence == Confidence.HIGH


def test_proposer_agent_role_finding_targets_prompt() -> None:
    proposer = AdjustmentProposer()
    finding = _finding(severity="warning", slice_dim="agent_role")
    proposals = proposer.propose(drift_signals=[], findings=[finding])
    assert proposals[0].adjustment_type == AdjustmentType.PROMPT_EDIT
    assert "fundamental" in proposals[0].target_path


# ---------------------------------------------------------------------------
# CounterfactualReport → ProposedAdjustment
# ---------------------------------------------------------------------------


def test_proposer_emits_synthesizer_proposal_when_bear_advantage_high() -> None:
    proposer = AdjustmentProposer()
    cf = _cf(bear_advantage=0.8)
    proposals = proposer.propose(drift_signals=[], counterfactual=cf)
    assert any(
        "synthesizer" in p.target_path and p.adjustment_type == AdjustmentType.LOGIC_CHANGE
        for p in proposals
    )


def test_proposer_emits_weight_proposal_when_single_model_wins() -> None:
    proposer = AdjustmentProposer()
    cf = _cf(ensemble_advantage=-0.10)
    proposals = proposer.propose(drift_signals=[], counterfactual=cf)
    assert any(
        "valuation_weights" in p.target_path and p.adjustment_type == AdjustmentType.WEIGHT_CHANGE
        for p in proposals
    )


def test_proposer_no_counterfactual_proposals_when_signals_neutral() -> None:
    proposer = AdjustmentProposer()
    cf = _cf(bear_advantage=0.0, ensemble_advantage=0.0)
    assert proposer.propose(drift_signals=[], counterfactual=cf) == []


def test_proposer_combines_all_three_sources() -> None:
    proposer = AdjustmentProposer()
    proposals = proposer.propose(
        drift_signals=[_drift(DriftSignalType.ACCURACY_DROP)],
        findings=[_finding(severity="warning")],
        counterfactual=_cf(bear_advantage=0.7),
    )
    assert len(proposals) >= 3
