"""Actual earnings vs prospectus prediction auto-compare per PROJECT_SPEC.md §3.11.

Triggered by ``event_detector`` when an earnings filing is detected.
Pulls the snapshot's prospectus extraction + the filing's actual numbers
and produces an ``EarningsComparison`` blob with 5 dimension diffs:

1. Revenue (same accounting standard)
2. Net profit / adjusted net profit
3. Gross margin
4. Operating KPIs (industry-specific, via ``mapping_rules.yaml``)
5. Segment performance (qualitative deviations list)

CLAUDE.md v1.2 constraint: "earnings_comparator 前 3 次必须
requires_human_review=True" — the comparator counts its own prior runs
to enforce this.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.enums import Confidence, EarningsAssessment
from ..common.logging import get_logger
from ..common.schemas import EarningsComparison, PredictionSnapshot
from ..data.models import EarningsComparisonRow

logger = get_logger(__name__)

FIRST_RUN_REVIEW_THRESHOLD = 3  # First 3 comparisons MUST flag requires_human_review

# Default mapping rules location (relative to repo root).
_DEFAULT_MAPPING_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "mapping_rules.yaml"


@dataclass(frozen=True)
class FilingNumbers:
    """The slice of an earnings filing we actually consume."""

    report_period: str  # 'FY2025' / 'H1-2025' / 'Q1-2025'
    filing_date: date
    actual_revenue: Decimal | None
    actual_net_profit: Decimal | None
    actual_gross_margin: Decimal | None
    extra_kpis: dict[str, Any]


def load_mapping_rules(path: Path | None = None) -> dict[str, Any]:
    """Parse mapping_rules.yaml; returns empty dict if absent (best-effort)."""
    target = path or _DEFAULT_MAPPING_RULES_PATH
    if not target.exists():
        logger.warning("mapping_rules_not_found", path=str(target))
        return {}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def _pct_deviation(actual: Decimal | None, predicted: Decimal | None) -> Decimal | None:
    """Returns (actual - predicted) / predicted, or None when either side is missing."""
    if actual is None or predicted is None or predicted == 0:
        return None
    return Decimal(str(float((actual - predicted) / predicted)))


def _assess(
    revenue_dev: Decimal | None,
    profit_dev: Decimal | None,
) -> EarningsAssessment:
    """Combine revenue + profit deviations into a single assessment band."""
    devs = [d for d in (revenue_dev, profit_dev) if d is not None]
    if not devs:
        return EarningsAssessment.IN_LINE
    avg = sum(devs) / len(devs)
    if avg < Decimal("-0.20"):
        return EarningsAssessment.SIGNIFICANT_MISS
    if avg < Decimal("-0.05"):
        return EarningsAssessment.MISS
    if avg > Decimal("0.05"):
        return EarningsAssessment.BEAT
    return EarningsAssessment.IN_LINE


class EarningsComparator:
    """Compares actual filings to prospectus extractions; persists comparisons."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        mapping_rules: dict[str, Any] | None = None,
    ) -> None:
        self._sf = session_factory
        self._rules = mapping_rules if mapping_rules is not None else load_mapping_rules()

    async def compare(
        self,
        *,
        snapshot: PredictionSnapshot,
        filing: FilingNumbers,
    ) -> EarningsComparison:
        """Build the comparison + persist; idempotent on (snapshot_id, report_period)."""
        # Pull predicted numbers from the snapshot's extraction (best-effort).
        predicted = self._predicted_from_extraction(snapshot)
        revenue_dev = _pct_deviation(filing.actual_revenue, predicted.get("revenue"))
        profit_dev = _pct_deviation(filing.actual_net_profit, predicted.get("net_profit"))
        margin_dev: Decimal | None = None
        predicted_margin = predicted.get("gross_margin")
        if filing.actual_gross_margin is not None and predicted_margin is not None:
            margin_dev = filing.actual_gross_margin - predicted_margin

        # First-3-runs review enforcement.
        prior_run_count = await self._prior_run_count()
        force_review = prior_run_count < FIRST_RUN_REVIEW_THRESHOLD
        qualitative = self._qualitative_deviations(snapshot, filing)

        cmp_obj = EarningsComparison(
            snapshot_id=snapshot.id,
            report_period=filing.report_period,
            filing_date=filing.filing_date,
            actual_revenue=filing.actual_revenue,
            predicted_revenue_from_prospectus=predicted.get("revenue"),
            revenue_deviation_pct=float(revenue_dev) if revenue_dev is not None else None,
            actual_net_profit=filing.actual_net_profit,
            predicted_net_profit=predicted.get("net_profit"),
            profit_deviation_pct=float(profit_dev) if profit_dev is not None else None,
            actual_gross_margin=(
                float(filing.actual_gross_margin)
                if filing.actual_gross_margin is not None
                else None
            ),
            predicted_gross_margin=(
                float(predicted_margin) if predicted_margin is not None else None
            ),
            margin_deviation_pp=float(margin_dev) if margin_dev is not None else None,
            qualitative_deviations=qualitative,
            overall_assessment=_assess(revenue_dev, profit_dev),
            confidence=Confidence.MEDIUM if not force_review else Confidence.LOW,
            notes="auto_generated",
            requires_human_review=force_review,
        )
        await self._persist(cmp_obj)
        return cmp_obj

    def _predicted_from_extraction(self, snapshot: PredictionSnapshot) -> dict[str, Decimal | None]:
        """Extract the most recent FY revenue / profit / margin from the prospectus.

        Mapping rules (per industry type) live in ``mapping_rules.yaml`` and let
        operators express adjusted-vs-IFRS handling. The MVP just reads the
        latest financial snapshot from the extraction.
        """
        ext = snapshot.input_data_snapshot.get("extraction", {})
        financials = ext.get("financial_snapshots") or []
        if not financials:
            return {"revenue": None, "net_profit": None, "gross_margin": None}
        latest = financials[-1]
        return {
            "revenue": _decimal_or_none(latest.get("revenue_rmb")),
            "net_profit": _decimal_or_none(
                latest.get("adjusted_net_profit_rmb") or latest.get("net_profit_rmb")
            ),
            "gross_margin": _decimal_or_none(latest.get("gross_margin")),
        }

    def _qualitative_deviations(
        self,
        snapshot: PredictionSnapshot,
        filing: FilingNumbers,
    ) -> list[str]:
        """Tag missing predictions / unexpected KPIs as qualitative notes."""
        notes: list[str] = []
        if filing.extra_kpis:
            notes.append(f"额外披露 KPI: {list(filing.extra_kpis.keys())}")
        if snapshot.decision.decision.value == "skip":
            notes.append("基线决策为 SKIP，本次披露仅供参考")
        return notes

    async def _prior_run_count(self) -> int:
        async with self._sf() as s:
            from sqlalchemy import func

            row = (
                await s.execute(select(func.count()).select_from(EarningsComparisonRow))
            ).scalar_one()
        return int(row or 0)

    async def _persist(self, cmp: EarningsComparison) -> UUID:
        async with self._sf() as s:
            row = {
                "id": _uuid.uuid4(),
                "snapshot_id": cmp.snapshot_id,
                "report_period": cmp.report_period,
                "filing_date": cmp.filing_date,
                "actual_revenue": cmp.actual_revenue,
                "predicted_revenue_from_prospectus": cmp.predicted_revenue_from_prospectus,
                "revenue_deviation_pct": (
                    Decimal(str(cmp.revenue_deviation_pct))
                    if cmp.revenue_deviation_pct is not None
                    else None
                ),
                "actual_net_profit": cmp.actual_net_profit,
                "predicted_net_profit": cmp.predicted_net_profit,
                "profit_deviation_pct": (
                    Decimal(str(cmp.profit_deviation_pct))
                    if cmp.profit_deviation_pct is not None
                    else None
                ),
                "actual_gross_margin": (
                    Decimal(str(cmp.actual_gross_margin))
                    if cmp.actual_gross_margin is not None
                    else None
                ),
                "predicted_gross_margin": (
                    Decimal(str(cmp.predicted_gross_margin))
                    if cmp.predicted_gross_margin is not None
                    else None
                ),
                "margin_deviation_pp": (
                    Decimal(str(cmp.margin_deviation_pp))
                    if cmp.margin_deviation_pp is not None
                    else None
                ),
                "qualitative_deviations": list(cmp.qualitative_deviations),
                "overall_assessment": cmp.overall_assessment.value,
                "confidence": cmp.confidence.value,
                "notes": cmp.notes,
                "requires_human_review": cmp.requires_human_review,
            }
            # Upsert on (snapshot_id, report_period) UNIQUE — idempotent.
            stmt = (
                pg_insert(EarningsComparisonRow)
                .values(row)
                .on_conflict_do_nothing(
                    index_elements=["snapshot_id", "report_period"],
                )
            )
            await s.execute(stmt)
            await s.commit()
        return UUID(str(row["id"]))


def _decimal_or_none(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (TypeError, ValueError):
        return None


__all__ = (
    "FIRST_RUN_REVIEW_THRESHOLD",
    "EarningsComparator",
    "FilingNumbers",
    "load_mapping_rules",
)
