"""Regulatory regime change points + market regime score.

Per PROJECT_SPEC.md §3.9 + ADR 0005 §1 + §3 + ADR 0013 §8a.

Two distinct concepts, both consumed by ``runner.py`` for sample-slicing:

1. **Regulatory regime** — discrete change points in HK IPO rules:
   - 2024-09-01: 18C market-cap threshold downward revision
   - 2025-08-04: new IPO pricing rules (35% clawback, Mechanism A/B)
   Backtests evaluate metrics separately by regime so a parameter change
   that helps post-2025-08 doesn't get credit for pre-2025-08 noise.

2. **Market regime score** — continuous 30-day median return of recently
   listed HK IPOs in the [t-120, t-30] window. NACS v8 found regime≥0
   filtered the all-sample IC from 0.057 → 0.247 with t=2.41 (ADR 0005
   §2). Surfaced to ``agents/policy_agent.py`` and used by
   ``valuation/ensemble.py`` as the SKIP gate.

NACS asset usage (ADR 0005 §1 strict): the ``market_environment_cache``
JSON fixture (54 monthly snapshots) is loaded lazily on first call and
cached at module level. NOT a PG table — this is reference data.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..common.enums import RegulatoryRegime
from ..common.logging import get_logger

logger = get_logger(__name__)

# Repo-root-anchored fixture path. The exporter in
# scripts/export_market_env_cache.py writes here.
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "fixtures" / "market_environment_cache.json"
)


# ---------------------------------------------------------------------------
# Regulatory regime change points
# ---------------------------------------------------------------------------

# Each entry: (effective_date, regime_after). Sorted by date ascending.
# Anything before the first effective_date is the "pre-" regime.
REGULATORY_CHANGE_POINTS: tuple[tuple[date, RegulatoryRegime], ...] = (
    (date(2025, 8, 4), RegulatoryRegime.POST_20250804),
)


def regulatory_regime_for(anchor: date) -> RegulatoryRegime:
    """Resolve the regulatory regime active on ``anchor``.

    Args:
        anchor: typically ``as_of_date`` from the walk-forward runner.

    Returns:
        The regime enum that was in effect (i.e. takes effect on or before
        ``anchor``).
    """
    active = RegulatoryRegime.PRE_20250804
    for effective_date, regime_after in REGULATORY_CHANGE_POINTS:
        if anchor >= effective_date:
            active = regime_after
    return active


# Calendar date of the 18C threshold revision — separately tracked
# because the threshold change doesn't itself flip RegulatoryRegime
# (it's an industry-specific tier rule), but slicing by it is useful.
CH18C_THRESHOLD_REVISION = date(2024, 9, 1)


# ---------------------------------------------------------------------------
# Market environment cache (NACS v8 inheritance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketEnvironment:
    """One monthly snapshot of the HK IPO market environment."""

    asof_month: date
    hsi_60d_return: float
    hsi_60d_vol_annualized: float
    hsi_60d_vol_pct_rank: float
    hsi_valuation_pct: float
    hk_ipo_30d_avg_d30: float
    hk_ipo_30d_breakage_rate: float
    southbound_30d_net_normalized: float
    sector_60d_vol_annualized: float
    source: str


@functools.lru_cache(maxsize=1)
def _load_market_env_cache() -> tuple[MarketEnvironment, ...]:
    """Read ``data/fixtures/market_environment_cache.json`` once per process."""
    if not _FIXTURE_PATH.exists():
        logger.warning(
            "market_env_cache_missing",
            path=str(_FIXTURE_PATH),
            hint="run scripts/export_market_env_cache.py",
        )
        return ()
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    parsed: list[MarketEnvironment] = []
    for row in rows:
        asof_raw = row["asof_month"]
        asof_d = (
            date.fromisoformat(asof_raw[:10])
            if isinstance(asof_raw, str)
            else asof_raw
        )
        parsed.append(
            MarketEnvironment(
                asof_month=asof_d,
                hsi_60d_return=float(row.get("hsi_60d_return") or 0.0),
                hsi_60d_vol_annualized=float(row.get("hsi_60d_vol_annualized") or 0.0),
                hsi_60d_vol_pct_rank=float(row.get("hsi_60d_vol_pct_rank") or 0.0),
                hsi_valuation_pct=float(row.get("hsi_valuation_pct") or 0.0),
                hk_ipo_30d_avg_d30=float(row.get("hk_ipo_30d_avg_d30") or 0.0),
                hk_ipo_30d_breakage_rate=float(row.get("hk_ipo_30d_breakage_rate") or 0.0),
                southbound_30d_net_normalized=float(row.get("southbound_30d_net_normalized") or 0.0),
                sector_60d_vol_annualized=float(row.get("sector_60d_vol_annualized") or 0.0),
                source=str(row.get("source") or "unknown"),
            )
        )
    return tuple(sorted(parsed, key=lambda r: r.asof_month))


def market_env_for(anchor: date) -> MarketEnvironment | None:
    """Return the closest-prior monthly snapshot to ``anchor``.

    Used to seed the market regime score when ``runner.py`` doesn't have
    direct iFind access (e.g. unit-test mode). Returns None when the
    fixture is empty or no snapshot precedes the anchor.
    """
    cache = _load_market_env_cache()
    candidates = [r for r in cache if r.asof_month <= anchor]
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# Market regime score (NACS v8 inheritance)
# ---------------------------------------------------------------------------


def regime_score_from_window(
    *,
    ipo_returns_30d: list[float],
) -> float:
    """NACS v8 regime score: median 30-day return of recently-listed IPOs.

    Args:
        ipo_returns_30d: per-IPO 30-day returns of IPOs that listed in
            the ``[t-120, t-30]`` window (caller resolves the window).

    Returns:
        Median of inputs, or 0.0 when the input list is empty.

    ADR 0005 §2 binding: regime ≥ 0 is the SKIP gate threshold. NACS v8
    empirics: regime≥0 subsample 60d IC = +0.247, t = +2.41.
    """
    if not ipo_returns_30d:
        return 0.0
    sorted_returns = sorted(ipo_returns_30d)
    mid = len(sorted_returns) // 2
    if len(sorted_returns) % 2 == 1:
        return float(sorted_returns[mid])
    return float((sorted_returns[mid - 1] + sorted_returns[mid]) / 2)


def regime_score_from_cache(anchor: date) -> float:
    """Convenience: returns the ``hk_ipo_30d_avg_d30`` of the closest
    prior monthly snapshot — proxies the regime score when live IPO
    returns aren't available (e.g. test fixtures).
    """
    env = market_env_for(anchor)
    return env.hk_ipo_30d_avg_d30 if env is not None else 0.0


# ---------------------------------------------------------------------------
# Sample slicing helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimeSlice:
    """A subset of samples sharing a regulatory regime."""

    regime: RegulatoryRegime
    sample_ipo_ids: tuple[str, ...]


def slice_by_regulatory_regime(
    samples: list[tuple[str, date]],
) -> list[RegimeSlice]:
    """Group ``[(ipo_id, anchor_date), ...]`` by regulatory regime.

    Used by ``runner.py`` so calibration evaluates metrics separately
    per regime. Returns a list (deterministic order by regime enum
    value) of ``RegimeSlice``.
    """
    by_regime: dict[RegulatoryRegime, list[str]] = {}
    for ipo_id, anchor in samples:
        regime = regulatory_regime_for(anchor)
        by_regime.setdefault(regime, []).append(ipo_id)
    return [
        RegimeSlice(regime=r, sample_ipo_ids=tuple(ids))
        for r, ids in sorted(by_regime.items(), key=lambda kv: kv[0].value)
    ]


def reset_cache() -> None:
    """Clear the lru_cached market_env fixture — testing only."""
    _load_market_env_cache.cache_clear()


__all__ = (
    "CH18C_THRESHOLD_REVISION",
    "REGULATORY_CHANGE_POINTS",
    "MarketEnvironment",
    "RegimeSlice",
    "market_env_for",
    "regime_score_from_cache",
    "regime_score_from_window",
    "regulatory_regime_for",
    "reset_cache",
    "slice_by_regulatory_regime",
)
