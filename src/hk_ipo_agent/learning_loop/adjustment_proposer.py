"""Adjustment proposer — Phase 10b per ADR 0015 + PROJECT_SPEC.md §3.12.

Maps 10a diagnostics (DriftSignal / AggregatedFinding / Counterfactual)
to ``ProposedAdjustment`` records. The output is **structured proposals
only** — they get written to ``prediction_reviews.proposed_adjustments``
JSONB with status=PROPOSED, never directly to config / prompt files.

The mapping is **heuristic** (not LLM-driven) to keep the proposer
deterministic and auditable. Concrete proposals require expert review
anyway (see ``adjustment_applier``).

Signal → AdjustmentType map (default; configurable):

| Signal | AdjustmentType | Target |
|---|---|---|
| ACCURACY_DROP | LOGIC_CHANGE | `config/synthesizer.yaml` (review trade-off logic) |
| VALUATION_BIAS | WEIGHT_CHANGE | `config/valuation_weights.yaml` (rebalance the slice) |
| BEAR_MISS_RATE_HIGH | PROMPT_EDIT | `prompts/agents/critic_bear.md` |
| AGENT_CALIBRATION_DRIFT | PROMPT_EDIT | `prompts/agents/{agent_role}.md` |

CLAUDE.md "no auto-apply" — writes only proposals; the applier enforces
the human gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ..common.enums import AdjustmentType, Confidence, DriftSignalType
from ..common.logging import get_logger
from ..common.schemas import DriftSignal, ProposedAdjustment
from .attribution_aggregator import AggregatedFinding
from .counterfactual import CounterfactualReport

logger = get_logger(__name__)


# Default target paths per signal type. Used as the proposed
# ``target_path``; reviewer can override.
DEFAULT_TARGETS: dict[DriftSignalType, str] = {
    DriftSignalType.ACCURACY_DROP: "config/synthesizer.yaml",
    DriftSignalType.VALUATION_BIAS: "config/valuation_weights.yaml",
    DriftSignalType.BEAR_MISS_RATE_HIGH: "prompts/agents/critic_bear.md",
    DriftSignalType.AGENT_CALIBRATION_DRIFT: "prompts/agents/{agent_role}.md",
    DriftSignalType.MISSING_FACTOR: "config/valuation_weights.yaml",
    DriftSignalType.REGIME_BREAK: "config/synthesizer.yaml",
}

DEFAULT_TYPES: dict[DriftSignalType, AdjustmentType] = {
    DriftSignalType.ACCURACY_DROP: AdjustmentType.LOGIC_CHANGE,
    DriftSignalType.VALUATION_BIAS: AdjustmentType.WEIGHT_CHANGE,
    DriftSignalType.BEAR_MISS_RATE_HIGH: AdjustmentType.PROMPT_EDIT,
    DriftSignalType.AGENT_CALIBRATION_DRIFT: AdjustmentType.PROMPT_EDIT,
    DriftSignalType.MISSING_FACTOR: AdjustmentType.FACTOR_ADD,
    DriftSignalType.REGIME_BREAK: AdjustmentType.LOGIC_CHANGE,
}


@dataclass(frozen=True)
class ProposerConfig:
    """Knobs for the proposer."""

    # Skip proposals when underlying signal sample_count is below this:
    min_sample_count: int = 10
    # Default confidence emitted when no per-signal override is set:
    default_confidence: Confidence = Confidence.LOW


class AdjustmentProposer:
    """Maps 10a diagnostics into ProposedAdjustment records.

    Stateless. The caller persists the output to
    ``prediction_reviews.proposed_adjustments`` (PG JSONB) — see the CLI
    in ``scripts/run_learning_cycle.py``.
    """

    def __init__(self, config: ProposerConfig | None = None) -> None:
        self._cfg = config or ProposerConfig()

    def propose(
        self,
        *,
        drift_signals: list[DriftSignal],
        findings: list[AggregatedFinding] | None = None,
        counterfactual: CounterfactualReport | None = None,
    ) -> list[ProposedAdjustment]:
        """Return a list of proposed adjustments — newest signal first.

        At least one input is required (validated lightly).
        """
        proposals: list[ProposedAdjustment] = []
        proposals.extend(self._from_drift_signals(drift_signals))
        proposals.extend(self._from_findings(findings or []))
        proposals.extend(self._from_counterfactual(counterfactual))
        return proposals

    # ------------------------------------------------------------------
    # DriftSignal → ProposedAdjustment
    # ------------------------------------------------------------------

    def _from_drift_signals(
        self, drift_signals: list[DriftSignal],
    ) -> list[ProposedAdjustment]:
        out: list[ProposedAdjustment] = []
        for signal in drift_signals:
            if signal.sample_count < self._cfg.min_sample_count:
                logger.info(
                    "drift_signal_sample_too_small",
                    signal_type=signal.signal_type.value,
                    n=signal.sample_count,
                )
                continue
            target_template = DEFAULT_TARGETS.get(
                signal.signal_type, "config/unknown.yaml"
            )
            target = (
                target_template.format(**signal.affected_dimensions)
                if "{" in target_template
                else target_template
            )
            adjustment_type = DEFAULT_TYPES.get(
                signal.signal_type, AdjustmentType.LOGIC_CHANGE,
            )
            evidence: list[UUID] = []
            for sid in signal.related_snapshot_ids:
                if isinstance(sid, UUID):
                    evidence.append(sid)
                else:
                    try:
                        evidence.append(UUID(str(sid)))
                    except (TypeError, ValueError):
                        continue
            out.append(
                ProposedAdjustment(
                    target_path=target,
                    adjustment_type=adjustment_type,
                    current_value=None,
                    proposed_value=None,
                    rationale=(
                        f"{signal.signal_type.value} drift detected: "
                        f"{signal.evidence}. "
                        f"Slice={signal.affected_dimensions}. "
                        f"metric={signal.metric_value:.3f} > "
                        f"threshold={signal.threshold:.3f}"
                    ),
                    evidence_snapshot_ids=evidence,
                    expected_impact=_impact_for(signal),
                    confidence=_confidence_for(signal),
                )
            )
        return out

    # ------------------------------------------------------------------
    # AggregatedFinding → ProposedAdjustment
    # ------------------------------------------------------------------

    def _from_findings(
        self, findings: list[AggregatedFinding],
    ) -> list[ProposedAdjustment]:
        out: list[ProposedAdjustment] = []
        for f in findings:
            if f.severity == "info":
                continue
            # Map slice_dimension → target file.
            if f.slice_dimension == "agent_role":
                target = f"prompts/agents/{f.slice_value}.md"
                adj_type = AdjustmentType.PROMPT_EDIT
            elif f.slice_dimension == "listing_type":
                target = "config/valuation_weights.yaml"
                adj_type = AdjustmentType.WEIGHT_CHANGE
            else:
                target = "config/synthesizer.yaml"
                adj_type = AdjustmentType.LOGIC_CHANGE
            evidence = list(f.related_snapshot_ids)
            confidence = (
                Confidence.HIGH if f.severity == "critical"
                else Confidence.MEDIUM
            )
            out.append(
                ProposedAdjustment(
                    target_path=target,
                    adjustment_type=adj_type,
                    current_value=None,
                    proposed_value=None,
                    rationale=(
                        f"{f.occurrences} reviews share primary_attribution="
                        f"'{f.primary_attribution}' in slice "
                        f"{f.slice_dimension}={f.slice_value} "
                        f"(share={f.share:.0%}, severity={f.severity})"
                    ),
                    evidence_snapshot_ids=evidence,
                    expected_impact=(
                        f"reduce systematic '{f.primary_attribution}' error "
                        f"in {f.slice_dimension}={f.slice_value}"
                    ),
                    confidence=confidence,
                )
            )
        return out

    # ------------------------------------------------------------------
    # CounterfactualReport → ProposedAdjustment
    # ------------------------------------------------------------------

    def _from_counterfactual(
        self, cf: CounterfactualReport | None,
    ) -> list[ProposedAdjustment]:
        if cf is None:
            return []
        out: list[ProposedAdjustment] = []
        if cf.if_bear.bear_advantage >= 0.50 and cf.if_bear.n_bull_won_bad >= 3:
            out.append(
                ProposedAdjustment(
                    target_path="config/synthesizer.yaml",
                    adjustment_type=AdjustmentType.LOGIC_CHANGE,
                    current_value=None,
                    proposed_value=None,
                    rationale=(
                        f"Counterfactual: Bear would have avoided "
                        f"{cf.if_bear.n_bear_would_have_avoided}/"
                        f"{cf.if_bear.n_bull_won_bad} bad outcomes that "
                        "Synthesizer let through. Reconsider bull/bear "
                        "trade-off weighting."
                    ),
                    evidence_snapshot_ids=[],
                    expected_impact="reduce false-positive PARTICIPATE decisions",
                    confidence=Confidence.MEDIUM,
                )
            )
        if cf.if_single_model.ensemble_advantage < -0.05:
            out.append(
                ProposedAdjustment(
                    target_path="config/valuation_weights.yaml",
                    adjustment_type=AdjustmentType.WEIGHT_CHANGE,
                    current_value=None,
                    proposed_value=None,
                    rationale=(
                        f"Counterfactual: single model "
                        f"'{cf.if_single_model.best_single_model}' has "
                        f"hit-rate {cf.if_single_model.best_single_hit_rate:.0%} "
                        f"vs ensemble {cf.if_single_model.ensemble_hit_rate:.0%} "
                        f"(advantage {-cf.if_single_model.ensemble_advantage:.1%}). "
                        "Ensemble blending may be over-fit."
                    ),
                    evidence_snapshot_ids=[],
                    expected_impact="improve ensemble hit-rate by reweighting",
                    confidence=Confidence.LOW,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confidence_for(signal: DriftSignal) -> Confidence:
    """Crit severity → MEDIUM; warning → LOW (matches CLAUDE.md
    conservatism — high LLM-uncertainty unless evidence is overwhelming)."""
    sev = (
        signal.severity.value if hasattr(signal.severity, "value")
        else str(signal.severity)
    )
    if sev == "critical":
        return Confidence.MEDIUM
    return Confidence.LOW


def _impact_for(signal: DriftSignal) -> str:
    """One-line natural-language description for the human reviewer."""
    return {
        DriftSignalType.ACCURACY_DROP: "improve decision accuracy by addressing the root cause",
        DriftSignalType.VALUATION_BIAS: "remove systematic over/under-prediction in the slice",
        DriftSignalType.BEAR_MISS_RATE_HIGH: "raise Bear's sensitivity to undisclosed risk",
        DriftSignalType.AGENT_CALIBRATION_DRIFT: "recalibrate the agent's confidence scoring",
        DriftSignalType.MISSING_FACTOR: "add the missing factor to the agent's prompt or model",
        DriftSignalType.REGIME_BREAK: "split or update the synthesizer logic for the new regime",
    }.get(signal.signal_type, "fix the detected drift")


# ===========================================================================
# Persistence helper (PG) — writes proposals into prediction_reviews
# ===========================================================================


async def persist_proposals_to_review(
    snapshot_id: UUID,
    proposals: list[ProposedAdjustment],
    session_factory: Any,
    *,
    reviewer: str = "system:learning_loop",
    primary_attribution: str = "learning_loop_proposal",
    review_checkpoint_day: int = 90,
) -> UUID:
    """Insert a new ``prediction_reviews`` row carrying these proposals.

    The row's ``adjustment_status`` starts as ``proposed``; applier
    requires status=accepted + reviewer non-null before any apply.

    Returns the new review_id.
    """
    from datetime import UTC as _UTC  # noqa: PLC0415
    from datetime import datetime as _dt  # noqa: PLC0415

    from ..common.enums import AdjustmentStatus  # noqa: PLC0415
    from ..data.models import PredictionReviewRow  # noqa: PLC0415

    proposals_json = [p.model_dump(mode="json") for p in proposals]
    async with session_factory() as s:
        row = PredictionReviewRow(
            snapshot_id=snapshot_id,
            review_checkpoint_day=review_checkpoint_day,
            reviewer=reviewer,
            primary_attribution=primary_attribution,
            proposed_adjustments=proposals_json,
            adjustment_status=AdjustmentStatus.PROPOSED.value,
            notes_md=f"Auto-proposed by learning_loop ({len(proposals)} adjustments)",
            created_at=_dt.now(_UTC),
            updated_at=_dt.now(_UTC),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        new_id: UUID = row.id
    logger.info(
        "learning_loop_proposals_persisted",
        review_id=str(new_id),
        n_proposals=len(proposals),
    )
    return new_id


__all__ = (
    "DEFAULT_TARGETS",
    "DEFAULT_TYPES",
    "AdjustmentProposer",
    "ProposerConfig",
    "persist_proposals_to_review",
)
