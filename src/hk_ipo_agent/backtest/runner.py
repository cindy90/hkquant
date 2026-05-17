"""Walk-forward backtest engine.

Per PROJECT_SPEC.md §3.9 + ADR 0005 §3 + ADR 0013 §8c.

The runner orchestrates a walk-forward evaluation over historical IPOs:

1. For each historical IPO, set ``as_of_date = pricing_date - 1`` and
   construct an ``AsOfDataProvider(as_of_date)`` — every read goes through
   the provider so no field post-dating ``as_of_date`` leaks into the
   prediction.

2. A ``BacktestScorer`` (Protocol) produces a ``decision_score`` from the
   provider's view. Two implementations ship:
     - ``V8LiteScorer`` — a lightweight regime / listing-type / cluster
       composite that matches NACS v8 empirics without LLM cost (the cost
       of running the full LangGraph pipeline 50+ times would be ~$250
       and is gated behind a separate adapter for offline runs).
     - ``FullPipelineScorer`` — placeholder hook for plugging the real
       orchestrator when needed; deferred to Phase 9 case studies.

3. Realized returns come from a separate ``RealizedReturnsFetcher`` —
   this is intentionally OUTSIDE the AsOfDataProvider's scope, because
   computing realized post-IPO returns requires reading data dated
   AFTER ``pricing_date`` (which the provider correctly refuses).

4. Samples are bundled into a ``BacktestRun`` and per-slice
   ``MetricsReport`` computed via the Phase 8b ``metrics`` module
   (main_board / regime_pass slices).

The CLI driver (``scripts/run_backtest.py``) wires PG-loading,
runner.run, calibration, and report writing together for the 50+ sample
end-to-end run.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from ..common.enums import ListingType, RegulatoryRegime
from ..common.exceptions import LookAheadError
from ..common.logging import get_logger
from .as_of_data import AsOfDataProvider
from .metrics import MetricsReport, compute_report
from .regime_detection import (
    regime_score_from_cache,
    regulatory_regime_for,
)

logger = get_logger(__name__)

# Canonical horizons evaluated for every IPO. These align with v8 baselines
# (5d / 30d / 60d / 180d) so monotonicity_constraint vs the v8 fixture
# Just Works.
DEFAULT_HORIZONS: tuple[str, ...] = ("5d", "30d", "60d", "180d")

# Regime Gate threshold (mirrors valuation/ensemble.py — ADR 0005 §2).
REGIME_GATE_THRESHOLD: float = 0.0


# ===========================================================================
# Dataclasses
# ===========================================================================


@dataclass(frozen=True)
class ScoreOutput:
    """A scorer's verdict at the as_of_date."""

    decision_score: float  # higher = more bullish
    regime_score: float
    regulatory_regime: RegulatoryRegime
    listing_type: ListingType | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BacktestInput:
    """Pre-resolved input for one historical IPO sample.

    Decoupling input loading from runner.run means unit tests can build
    BacktestInput in-memory without PG, while the CLI driver builds them
    from ``ipo_events`` + ``ipo_postmarket``.
    """

    ipo_id: UUID
    pricing_date: date
    stock_code: str | None
    listing_type: ListingType | None
    realized_returns: dict[str, float]  # {"5d": 0.05, "30d": 0.12, ...}
    # Cornerstone disclosure count at as_of_date — used by V8LiteScorer
    # cluster bonus; loaded by the CLI driver alongside realized returns.
    cornerstone_count: int = 0


@dataclass(frozen=True)
class BacktestSample:
    """Result of scoring one historical IPO + its realized returns."""

    ipo_id: UUID
    stock_code: str | None
    listing_type: ListingType | None
    pricing_date: date
    as_of_date: date
    decision_score: float
    realized_returns: dict[str, float]
    regime_score: float
    regulatory_regime: RegulatoryRegime
    notes: tuple[str, ...]

    @property
    def regime_pass(self) -> bool:
        return self.regime_score >= REGIME_GATE_THRESHOLD


@dataclass(frozen=True)
class BacktestRun:
    """Aggregated result of a walk-forward run."""

    run_id: UUID
    started_at: datetime
    finished_at: datetime
    samples: tuple[BacktestSample, ...]
    metrics_by_label: dict[str, MetricsReport]
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def n_total(self) -> int:
        return len(self.samples)

    @property
    def n_regime_pass(self) -> int:
        return sum(1 for s in self.samples if s.regime_pass)


# ===========================================================================
# Scorer protocol + V8-lite implementation
# ===========================================================================


class BacktestScorer(Protocol):
    """A scoring strategy that converts an AsOfDataProvider view into a
    decision_score. Implementations must respect the provider's
    leak-prevention contract."""

    async def score(
        self,
        provider: AsOfDataProvider,
        sample_input: BacktestInput,
    ) -> ScoreOutput: ...


# Listing-type base scores (NACS v8 empirical ordering — ADR 0005 §3).
# 18C-PRE / 18A pre-commercial tend to do worse than commercial /
# mainboard tech in the v8 sample. AH is a special tier with its own
# premium dynamics. These are seed values — calibration may revise.
_LISTING_TYPE_BASE: dict[ListingType, float] = {
    ListingType.CH18C_COMMERCIALIZED: 0.50,
    ListingType.MAINBOARD_TECH: 0.45,
    ListingType.AH_DUAL: 0.40,
    ListingType.MAINBOARD_OTHER: 0.30,
    ListingType.CH18C_PRE_COMMERCIAL: 0.20,
    ListingType.CH18A_BIOTECH: 0.20,
}


class V8LiteScorer:
    """Lightweight v8-style scorer — no LLM, deterministic, fast.

    decision_score = base(listing_type) + cluster_bonus + regime_bonus

    Where:
    - base: fixed ladder per listing_type (NACS v8 empirics).
    - cluster_bonus: 0.05 per cornerstone investor disclosed at as_of_date,
      capped at 0.20 (matches v8 ``Cluster Bonus`` ≥2 effect).
    - regime_bonus: +0.10 when ``regime_score ≥ 0`` else –0.20 (Regime Gate
      is a soft signal here; the hard gate kicks in via samples' ``regime_pass``
      property at metrics time).
    """

    def __init__(
        self,
        *,
        cluster_bonus_per_investor: float = 0.05,
        cluster_bonus_cap: float = 0.20,
        regime_bonus_pass: float = 0.10,
        regime_bonus_fail: float = -0.20,
    ) -> None:
        self._cluster_unit = cluster_bonus_per_investor
        self._cluster_cap = cluster_bonus_cap
        self._regime_pass = regime_bonus_pass
        self._regime_fail = regime_bonus_fail

    async def score(
        self,
        provider: AsOfDataProvider,
        sample_input: BacktestInput,
    ) -> ScoreOutput:
        notes: list[str] = []
        as_of = provider.as_of_date
        regulatory = regulatory_regime_for(as_of)
        regime = regime_score_from_cache(as_of)

        base = (
            _LISTING_TYPE_BASE.get(sample_input.listing_type, 0.30)
            if sample_input.listing_type is not None
            else 0.30
        )
        cluster_bonus = min(sample_input.cornerstone_count * self._cluster_unit, self._cluster_cap)
        regime_bonus = self._regime_pass if regime >= REGIME_GATE_THRESHOLD else self._regime_fail
        if regime < REGIME_GATE_THRESHOLD:
            notes.append(
                f"regime_score={regime:.3f} < {REGIME_GATE_THRESHOLD}; "
                "soft penalty applied (hard gate at metrics layer)"
            )

        decision_score = base + cluster_bonus + regime_bonus
        return ScoreOutput(
            decision_score=decision_score,
            regime_score=regime,
            regulatory_regime=regulatory,
            listing_type=sample_input.listing_type,
            notes=tuple(notes),
        )


# ===========================================================================
# Realized-returns fetcher type
# ===========================================================================


RealizedReturnsFetcher = Callable[[UUID, tuple[str, ...]], Awaitable[dict[str, float]]]
"""``(ipo_id, horizons) → {horizon: return}``.

Returns can be missing — the runner just skips unfilled horizons. The
default PG-backed loader is in ``runner.fetch_realized_returns_from_pg``
but tests inject a synthetic version.
"""


# ===========================================================================
# Walk-forward harness
# ===========================================================================


async def run_walk_forward(
    inputs: Iterable[BacktestInput],
    *,
    scorer: BacktestScorer,
    session_factory: Any,  # async_sessionmaker[AsyncSession]
    horizons: tuple[str, ...] = DEFAULT_HORIZONS,
    config_snapshot: dict[str, Any] | None = None,
) -> BacktestRun:
    """Score each IPO with an as_of provider and bundle into a run.

    The harness is deliberately small: AsOfDataProvider does the heavy
    leak-prevention lift, and the scorer carries the business logic.
    Realized returns come pre-resolved on each ``BacktestInput`` — the
    CLI driver loads them from PG, tests inject them synthetically.
    """
    started = datetime.now(UTC)
    samples: list[BacktestSample] = []
    for sample_input in inputs:
        as_of = sample_input.pricing_date - timedelta(days=1)
        try:
            provider = AsOfDataProvider(
                as_of_date=as_of,
                session_factory=session_factory,
            )
        except LookAheadError as exc:
            logger.warning(
                "backtest_skip_future_pricing",
                ipo_id=str(sample_input.ipo_id),
                as_of=str(as_of),
                error=str(exc),
            )
            continue
        score = await scorer.score(provider, sample_input)
        samples.append(
            BacktestSample(
                ipo_id=sample_input.ipo_id,
                stock_code=sample_input.stock_code,
                listing_type=sample_input.listing_type,
                pricing_date=sample_input.pricing_date,
                as_of_date=as_of,
                decision_score=score.decision_score,
                realized_returns=sample_input.realized_returns,
                regime_score=score.regime_score,
                regulatory_regime=score.regulatory_regime,
                notes=score.notes,
            )
        )
    finished = datetime.now(UTC)

    metrics_by_label = _compute_metrics_per_slice(samples, horizons=horizons)

    return BacktestRun(
        run_id=uuid.uuid4(),
        started_at=started,
        finished_at=finished,
        samples=tuple(samples),
        metrics_by_label=metrics_by_label,
        config_snapshot=config_snapshot or {},
    )


def _compute_metrics_per_slice(
    samples: list[BacktestSample],
    *,
    horizons: tuple[str, ...],
) -> dict[str, MetricsReport]:
    """Compute MetricsReport for each canonical slice."""
    slices: dict[str, list[BacktestSample]] = {
        "main_board": list(samples),
        "regime_pass": [s for s in samples if s.regime_pass],
    }
    out: dict[str, MetricsReport] = {}
    for label, slice_samples in slices.items():
        per_horizon: dict[str, tuple[list[float], list[float]]] = {}
        for h in horizons:
            predicted: list[float] = []
            realized: list[float] = []
            for s in slice_samples:
                r = s.realized_returns.get(h)
                if r is None or _isnan(r):
                    continue
                predicted.append(s.decision_score)
                realized.append(r)
            if predicted:
                per_horizon[h] = (predicted, realized)
        out[label] = compute_report(label=label, per_horizon=per_horizon)
    return out


def _isnan(x: float) -> bool:
    try:
        return math.isnan(x)
    except (TypeError, ValueError):
        return False


# ===========================================================================
# PG-backed input loader (used by scripts/run_backtest.py)
# ===========================================================================


async def load_backtest_inputs_from_pg(
    session_factory: Any,
    *,
    min_pricing_date: date | None = None,
    horizons: tuple[str, ...] = DEFAULT_HORIZONS,
) -> list[BacktestInput]:
    """Load eligible historical IPOs + realized returns from PG.

    Eligibility:
    - ``pricing_date`` present (otherwise nothing to backtest)
    - Either ``ipo_postmarket.returns_by_day`` OR the denormalized
      day1/5/22/126/252 scalars present (we fall back to scalars when
      the JSONB column wasn't backfilled)
    """
    from sqlalchemy import select as sa_select

    from ..data.models import IPOEvent, IPOPostMarket

    inputs: list[BacktestInput] = []
    async with session_factory() as s:
        stmt = sa_select(IPOEvent).where(IPOEvent.pricing_date.is_not(None))
        if min_pricing_date is not None:
            stmt = stmt.where(IPOEvent.pricing_date >= min_pricing_date)
        events = list((await s.execute(stmt)).scalars().all())
        for ev in events:
            pm = (
                await s.execute(sa_select(IPOPostMarket).where(IPOPostMarket.ipo_id == ev.id))
            ).scalar_one_or_none()
            realized = _coerce_returns(pm, horizons) if pm is not None else {}
            if not realized:
                continue
            listing_type = _coerce_listing_type(ev.listing_type)
            inputs.append(
                BacktestInput(
                    ipo_id=ev.id,
                    pricing_date=ev.pricing_date,
                    stock_code=ev.stock_code,
                    listing_type=listing_type,
                    realized_returns=realized,
                    cornerstone_count=0,  # filled by caller if needed
                )
            )
    return inputs


def _coerce_returns(
    pm: Any,
    horizons: tuple[str, ...],
) -> dict[str, float]:
    """Pull returns out of ``ipo_postmarket`` in either format."""
    out: dict[str, float] = {}
    # Preferred: JSONB returns_by_day = {"1": "0.05", "5": "0.12", ...}
    raw = getattr(pm, "returns_by_day", None) or {}
    if raw:
        for h in horizons:
            key = h.rstrip("d")
            if key in raw and raw[key] is not None:
                out[h] = float(raw[key])
        if out:
            return out
    # Fallback: denormalized scalars.
    scalar_map = {
        "5d": getattr(pm, "day5_return", None),
        "22d": getattr(pm, "day22_return", None),
        "126d": getattr(pm, "day126_return", None),
        "252d": getattr(pm, "day252_return", None),
        # Best-effort horizon aliasing:
        "30d": getattr(pm, "day22_return", None),  # 22 ≈ 30 calendar days
        "60d": None,  # No direct 60d scalar; rely on JSONB.
        "180d": getattr(pm, "day126_return", None),  # 126 ≈ 180 calendar days
    }
    for h in horizons:
        val = scalar_map.get(h)
        if val is not None:
            out[h] = float(val)
    return out


def _coerce_listing_type(raw: str | None) -> ListingType | None:
    if raw is None:
        return None
    try:
        return ListingType(raw)
    except ValueError:
        return None


# ===========================================================================
# Run persistence (ADR 0013 §8d — reuse prediction_snapshots, no new table)
# ===========================================================================


# Sentinel decision used to mark a snapshot as the product of a
# backtest run rather than a production analysis. Routers filter on
# this + the ``backtest_run_id`` config key.
_BACKTEST_DECISION_MARKER: str = "BACKTEST_ONLY"


async def persist_run_to_pg(
    run: BacktestRun,
    session_factory: Any,
    *,
    system_version: str = "phase-8c.v8lite",
) -> int:
    """Write one ``prediction_snapshots`` row per sample, group-tagged by run.

    ADR 0013 §8d binding: backtest runs are stored as a GROUP BY view
    over ``prediction_snapshots`` filtered on
    ``config_snapshot->>'backtest_run_id'``. The router queries this
    same shape.

    Per CLAUDE.md prediction-lifecycle:
    - Each row is immutable (DB trigger blocks UPDATE/DELETE).
    - The decision is marked ``BACKTEST_ONLY`` so production dashboards
      can filter these out.
    - The unique constraint on (ipo_id, as_of_date, prospectus_version)
      means re-running the same backtest with the same as_of_date will
      conflict; we use ``prospectus_version = "backtest:{short_run_id}"``
      to keep distinct runs separable.

    Returns the count of rows written.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from ..data.models import PredictionSnapshotRow

    short_run = str(run.run_id)[:8]
    rows_written = 0
    async with session_factory() as s:
        for sample in run.samples:
            row = PredictionSnapshotRow(
                ipo_id=sample.ipo_id,
                as_of_date=sample.as_of_date,
                prospectus_version=f"backtest:{short_run}",
                input_data_hash="backtest-no-hash",
                input_data_snapshot={
                    "stock_code": sample.stock_code,
                    "listing_type": (sample.listing_type.value if sample.listing_type else None),
                    "pricing_date": sample.pricing_date.isoformat(),
                    "regulatory_regime": sample.regulatory_regime.value,
                },
                agent_outputs={},
                valuation_output={
                    "decision_score": sample.decision_score,
                    "regime_score": sample.regime_score,
                    "regime_pass": sample.regime_pass,
                },
                debate_output={},
                decision={
                    "decision": _BACKTEST_DECISION_MARKER,
                    "confidence": 0.0,
                    "rationale": (f"backtest sample (run_id={short_run}); no live agent debate"),
                    "realized_returns": sample.realized_returns,
                },
                system_version=system_version,
                model_versions={"scorer": "V8LiteScorer"},
                config_snapshot={
                    "backtest_run_id": str(run.run_id),
                    "horizons": list(
                        run.metrics_by_label.get(
                            "main_board",
                            compute_report(label="main_board", per_horizon={}),
                        ).horizons.keys()
                    ),
                    "scorer": "V8LiteScorer",
                    **run.config_snapshot,
                },
                total_cost_usd=None,
                runtime_seconds=None,
                created_at=_dt.now(_UTC),
            )
            s.add(row)
            rows_written += 1
        await s.commit()
    logger.info(
        "backtest_run_persisted",
        run_id=str(run.run_id),
        rows=rows_written,
    )
    return rows_written


__all__ = (
    "DEFAULT_HORIZONS",
    "REGIME_GATE_THRESHOLD",
    "BacktestInput",
    "BacktestRun",
    "BacktestSample",
    "BacktestScorer",
    "RealizedReturnsFetcher",
    "ScoreOutput",
    "V8LiteScorer",
    "load_backtest_inputs_from_pg",
    "persist_run_to_pg",
    "run_walk_forward",
)

# Suppress unused-import noise (asyncio used for type hints).
_ = asyncio
