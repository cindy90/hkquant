"""Phase 10c — monthly learning cycle CLI per ADR 0015 + spec §3.12.

Workflow (one execution per month, typically driven by Airflow):

1. Load completed predictions from PG (prediction_snapshots JOIN
   prediction_outcomes) within the trailing window.
2. Run drift_detector → DriftSignal[].
3. Run attribution_aggregator → AggregatedFinding[].
4. Run counterfactual → CounterfactualReport.
5. Run adjustment_proposer → ProposedAdjustment[].
6. Persist proposals into a fresh prediction_reviews row
   (adjustment_status=PROPOSED).
7. Render monthly markdown report to reports/learning/.

**This CLI does NOT auto-apply.** All proposals require human review
via ``scripts/review_proposals.py`` + ``adjustment_applier.apply_review``.

Usage::

    uv run python scripts/run_learning_cycle.py
    uv run python scripts/run_learning_cycle.py --period 2026-05
    uv run python scripts/run_learning_cycle.py --window-days 90 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import (
    AdjustmentStatus,
    ListingType,
    RegulatoryRegime,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.learning_loop.adjustment_proposer import (
    AdjustmentProposer,
    persist_proposals_to_review,
)
from hk_ipo_agent.learning_loop.attribution_aggregator import (
    AttributionAggregator,
    ReviewRecord,
)
from hk_ipo_agent.learning_loop.counterfactual import run_counterfactual
from hk_ipo_agent.learning_loop.drift_detector import (
    DriftDetector,
    OutcomeWindowSample,
)
from hk_ipo_agent.learning_loop.reports import (
    AppliedAdjustmentRow,
    CalibrationStateRow,
    LearningReport,
    PendingProposalRow,
    period_label_for,
    write_report,
)

# ===========================================================================
# Loaders
# ===========================================================================


async def _load_outcome_samples(
    sf,
    *,
    since: datetime,
) -> list[OutcomeWindowSample]:
    """Pull completed predictions + their outcomes from PG."""
    from hk_ipo_agent.data.models import (
        PredictionOutcomeRow,
        PredictionSnapshotRow,
    )

    samples: list[OutcomeWindowSample] = []
    async with sf() as s:
        stmt = (
            select(PredictionSnapshotRow, PredictionOutcomeRow)
            .join(
                PredictionOutcomeRow,
                PredictionOutcomeRow.snapshot_id == PredictionSnapshotRow.id,
            )
            .where(PredictionSnapshotRow.created_at >= since)
            .where(PredictionOutcomeRow.checkpoint_day >= 30)
        )
        for snap, outcome in (await s.execute(stmt)).all():
            input_data = snap.input_data_snapshot or {}
            try:
                lt = ListingType(input_data.get("listing_type"))
            except (ValueError, TypeError):
                lt = None
            try:
                regime = RegulatoryRegime(input_data.get("regulatory_regime"))
            except (ValueError, TypeError):
                regime = RegulatoryRegime.PRE_20250804

            # R3-2 — extract the 4 fields that drive valuation_bias /
            # bear_miss_rate / agent_calibration_drift sub-detectors from
            # the snapshot's JSONB columns. Pre-fix these were hard-coded
            # to None / {}, so 3 of 4 sub-detectors silently never fired
            # and "no signals" in the learning report was a false negative,
            # not a sign of model stability. See PLAN R3-2.
            samples.append(
                OutcomeWindowSample(
                    snapshot_id=str(snap.id),
                    listing_type=lt,
                    regulatory_regime=regime,
                    decision_correct=outcome.decision_correct,
                    predicted_median_price=_extract_predicted_price(snap.decision),
                    realized_price_at_60d=_extract_realized_price(outcome),
                    bear_flagged_risk=_extract_bear_flagged_risk(snap.debate_output),
                    realized_outcome_negative=((outcome.return_since_listing or 0) < 0),
                    agent_scores=_extract_agent_scores(snap.agent_outputs),
                    agent_realized_hits=_extract_agent_realized_hits(
                        snap.agent_outputs, outcome.decision_correct
                    ),
                )
            )
    return samples


# ---------------------------------------------------------------------------
# R3-2 — JSONB field extractors. These are intentionally permissive: any
# malformed JSON path returns None / empty so missing-field cases just
# drop out of the relevant sub-detector instead of crashing the cycle.
# ---------------------------------------------------------------------------


def _extract_predicted_price(decision_jsonb: dict[str, Any] | None) -> float | None:
    """Pull the snapshot's predicted fair price for valuation_bias detector.

    Looks for ``decision.price_range_fair`` (Decimal serialized as string
    in JSONB). Falls back to ``price_range_low``/``price_range_high``
    midpoint if the fair value is missing.
    """
    if not decision_jsonb:
        return None
    for key in ("price_range_fair", "price_range_median"):
        val = decision_jsonb.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    lo = decision_jsonb.get("price_range_low")
    hi = decision_jsonb.get("price_range_high")
    if lo is not None and hi is not None:
        try:
            return (float(lo) + float(hi)) / 2.0
        except (ValueError, TypeError):
            return None
    return None


def _extract_realized_price(outcome: Any) -> float | None:
    """Compute the realized price at the outcome checkpoint.

    The outcome row stores returns (cumulative return_since_listing),
    not absolute prices. The drift detector compares predicted_median_price
    against realized_price_at_60d, so we approximate: if the outcome row
    carries an ``actual_price`` JSONB extension that's preferred; else
    we fall back to None so the valuation_bias slice for this sample is
    dropped rather than miscounted.

    Phase 7.5b stores cumulative return only; ADR 0007 left absolute
    price as a JSONB extension for future use.
    """
    extras = getattr(outcome, "actual_price", None)
    if extras is not None:
        try:
            return float(extras)
        except (ValueError, TypeError):
            return None
    return None


def _extract_bear_flagged_risk(debate_jsonb: dict[str, Any] | None) -> bool | None:
    """Did the bear agent flag risk for this prediction?

    Heuristic: the debate_output JSONB carries ``rounds[*].bear_argument``
    text. If any round contains explicit risk vocabulary (negative
    keywords such as 风险 / 下行 / fragile / weakness), we count it as
    bear-flagged. Pre-R3-2 this was hard-coded to None so the bear_miss_rate
    sub-detector saw 0% miss rate trivially.

    Returns None if the debate field is empty (sample drops out of slice).
    """
    if not debate_jsonb:
        return None
    rounds = debate_jsonb.get("rounds") or []
    if not rounds:
        # Could also check final_consensus for negative language.
        text = (debate_jsonb.get("final_consensus") or "").lower()
        return _has_negative_signal(text) if text else None
    for r in rounds:
        bear_text = (r.get("bear_argument") or "").lower()
        if _has_negative_signal(bear_text):
            return True
    return False


_NEGATIVE_KEYWORDS_ZH = ("风险", "下行", "亏损", "回撤", "破发", "高估", "不确定")
_NEGATIVE_KEYWORDS_EN = ("risk", "downside", "weakness", "fragile", "overvalued", "concern")


def _has_negative_signal(text: str) -> bool:
    return any(kw in text for kw in _NEGATIVE_KEYWORDS_ZH) or any(
        kw in text for kw in _NEGATIVE_KEYWORDS_EN
    )


def _extract_agent_scores(agent_outputs_jsonb: dict[str, Any] | None) -> dict[str, float]:
    """Pull ``agent_role -> overall_score`` mapping for calibration drift detector.

    Snapshot stores ``agent_outputs[role] = {overall_score, scores, key_findings, ...}``.
    """
    if not agent_outputs_jsonb:
        return {}
    out: dict[str, float] = {}
    for role, payload in agent_outputs_jsonb.items():
        if not isinstance(payload, dict):
            continue
        score = payload.get("overall_score")
        if score is None:
            continue
        try:
            out[role] = float(score)
        except (ValueError, TypeError):
            continue
    return out


def _extract_agent_realized_hits(
    agent_outputs_jsonb: dict[str, Any] | None,
    decision_correct: bool | None,
) -> dict[str, bool]:
    """Approximate per-agent hit-rate by attributing the overall decision
    correctness back to every agent that scored ≥ 70 (i.e. expressed
    confidence in the call). High-confidence agents whose calls were
    wrong drive the calibration_drift sub-detector.

    Pre-R3-2 this was {} so calibration_drift trivially saw "no
    high-confidence misses" and never fired.

    NOTE: Phase 10c+ should replace this heuristic with agent-level
    realized hits tracked separately on the outcome row.
    """
    if not agent_outputs_jsonb or decision_correct is None:
        return {}
    out: dict[str, bool] = {}
    for role, payload in agent_outputs_jsonb.items():
        if not isinstance(payload, dict):
            continue
        score = payload.get("overall_score")
        try:
            score_f = float(score) if score is not None else None
        except (ValueError, TypeError):
            score_f = None
        if score_f is None or score_f < 70.0:
            # Not "high confidence" — exclude from calibration drift.
            continue
        out[role] = bool(decision_correct)
    return out


async def _load_review_records(
    sf,
    *,
    since: datetime,
) -> list[ReviewRecord]:
    """Pull human reviews within the window for attribution aggregation."""
    from hk_ipo_agent.data.models import PredictionReviewRow

    records: list[ReviewRecord] = []
    async with sf() as s:
        stmt = select(PredictionReviewRow).where(PredictionReviewRow.created_at >= since)
        for row in (await s.execute(stmt)).scalars().all():
            if not row.primary_attribution:
                continue
            records.append(
                ReviewRecord(
                    review_id=row.id,
                    snapshot_id=row.snapshot_id,
                    primary_attribution=row.primary_attribution,
                    listing_type=None,
                    agent_role=None,
                    created_at=row.created_at,
                )
            )
    return records


async def _load_pending_proposals(sf) -> list[PendingProposalRow]:
    from hk_ipo_agent.data.models import PredictionReviewRow

    out: list[PendingProposalRow] = []
    async with sf() as s:
        stmt = select(PredictionReviewRow).where(
            PredictionReviewRow.adjustment_status == AdjustmentStatus.PROPOSED.value,
        )
        for row in (await s.execute(stmt)).scalars().all():
            proposals = row.proposed_adjustments or []
            out.append(
                PendingProposalRow(
                    review_id=str(row.id),
                    snapshot_id=str(row.snapshot_id),
                    proposal_count=len(proposals),
                    primary_attribution=row.primary_attribution,
                    created_at=row.created_at,
                )
            )
    return out


async def _load_applied_adjustments(sf) -> list[AppliedAdjustmentRow]:
    from hk_ipo_agent.data.models import (
        ConfigVersionRow,
    )

    out: list[AppliedAdjustmentRow] = []
    async with sf() as s:
        stmt = select(ConfigVersionRow).where(
            ConfigVersionRow.change_type == "learning_loop_applied",
        )
        for row in (await s.execute(stmt)).scalars().all():
            out.append(
                AppliedAdjustmentRow(
                    review_id=str(row.source_review_id or "—"),
                    applied_version=row.version,
                    applied_at=row.applied_at,
                    target_path=row.target_path,
                    effect="unknown",  # would need post-hoc analysis
                )
            )
    return out


def _compute_calibration_rows(
    samples: list[OutcomeWindowSample],
) -> list[CalibrationStateRow]:
    """Trivial: aggregate decision_correct across all samples + slice
    them by lookback-days. Returns 3 rows (30/60/90d) — using sample
    count as a proxy (we don't have per-sample creation_date here in
    the dataclass, so the slicing is illustrative)."""
    if not samples:
        return []
    n = len(samples)
    correct = [s for s in samples if s.decision_correct]
    acc = len(correct) / n
    return [
        CalibrationStateRow(
            window="30d",
            n_samples=n,
            accuracy=acc,
            avg_decision_correct=acc,
        ),
        CalibrationStateRow(
            window="60d",
            n_samples=n,
            accuracy=acc,
            avg_decision_correct=acc,
        ),
        CalibrationStateRow(
            window="90d",
            n_samples=n,
            accuracy=acc,
            avg_decision_correct=acc,
        ),
    ]


# ===========================================================================
# Main flow
# ===========================================================================


async def _amain(args: argparse.Namespace) -> int:
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        since = datetime.now(UTC) - timedelta(days=args.window_days)
        print(f"[learning-cycle] window={args.window_days}d → since={since.date()}")

        outcomes = await _load_outcome_samples(sf, since=since)
        reviews = await _load_review_records(sf, since=since)
        print(f"[learning-cycle] loaded {len(outcomes)} outcomes / {len(reviews)} reviews")

        detector = DriftDetector()
        drift_signals = detector.detect(outcomes)
        print(f"[learning-cycle] drift signals: {len(drift_signals)}")

        aggregator = AttributionAggregator()
        findings = aggregator.aggregate(reviews)
        print(f"[learning-cycle] findings: {len(findings)}")

        # Counterfactual needs richer per-sample state we don't have here;
        # passing empty samples → trivial neutral report.
        counterfactual = run_counterfactual([])

        proposer = AdjustmentProposer()
        proposals = proposer.propose(
            drift_signals=drift_signals,
            findings=findings,
            counterfactual=counterfactual,
        )
        print(f"[learning-cycle] proposals: {len(proposals)}")

        # Persist proposals (one review row per snapshot signal — for
        # MVP we attach all to a sentinel snapshot if any exist; full
        # implementation would map proposal → snapshot via evidence ids).
        review_id: UUID | None = None
        if proposals and outcomes and not args.dry_run:
            first_snap_id = UUID(outcomes[0].snapshot_id)
            review_id = await persist_proposals_to_review(
                first_snap_id,
                proposals,
                sf,
            )
            print(
                f"[learning-cycle] persisted proposals into review "
                f"{review_id} (status=PROPOSED, awaiting human review)"
            )
        elif args.dry_run:
            print("[learning-cycle] --dry-run: skipped proposal persistence")

        pending = await _load_pending_proposals(sf)
        applied = await _load_applied_adjustments(sf)

        period = args.period or period_label_for(date.today())
        report = LearningReport(
            period_label=period,
            calibration_rows=_compute_calibration_rows(outcomes),
            drift_signals=drift_signals,
            findings=findings,
            counterfactual=counterfactual,
            pending_proposals=pending,
            applied_adjustments=applied,
            notes=[
                f"window={args.window_days} days",
                f"persisted_review_id={review_id}" if review_id else "no proposals persisted",
                "next step: review proposals via scripts/review_proposals.py",
            ],
        )
        out_path = write_report(report, out_dir=Path(args.report_dir))
        print(f"[learning-cycle] report → {out_path}")
        return 0
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 10c monthly learning cycle")
    p.add_argument(
        "--window-days",
        type=int,
        default=90,
        help="Lookback window (days). Default: 90.",
    )
    p.add_argument(
        "--period",
        type=str,
        default=None,
        help="Override period label (YYYY-MM). Defaults to current month.",
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/learning"),
        help="Where to write the markdown report. Default: reports/learning/",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run diagnostics but don't persist proposals into PG.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
