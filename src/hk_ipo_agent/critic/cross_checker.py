"""Cross-checker — compares this IPO against historical analogues.

Per PROJECT_SPEC.md §3.8 (``cross_check`` node). The cross-checker scans
the NACS-imported ``ipo_events`` + ``ipo_postmarket`` tables for IPOs
with similar industry / listing_type / size, and surfaces post-IPO
outcome stats (60d return / drawdown / cornerstone retention) so the
synthesizer can sanity-check its own forecasts.

Phase 6 implementation:
- If a DB session is wired in via ``ctx.kb_tool.market_env_cache()`` or
  similar, pull stats.
- Else (test / no DB): return an empty ``CrossCheckResult`` and add a
  note to ``state.cross_check_notes``.

This is intentionally minimal in Phase 6 — Phase 8 (calibration) will
take this much further with IC / L-S analytics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common.enums import ListingType


@dataclass
class CrossCheckResult:
    """Aggregate stats from historical analogues."""

    sample_size: int
    median_60d_return: float | None
    median_drawdown: float | None
    notes: list[str]


def cross_check(
    *,
    listing_type: ListingType,
    industry_code: str,
    historical_records: list[dict[str, Any]] | None = None,
) -> CrossCheckResult:
    """Compute median 60d return + drawdown across historical analogues.

    ``historical_records`` shape (each dict):
        {
            "listing_type": str (matches ListingType.value),
            "industry_code": str,
            "return_60d": float,
            "max_drawdown_60d": float,
        }

    Filters to records with matching ``listing_type`` and same
    ``industry_code`` prefix (first 4 chars — coarse GICS-L2 alignment).
    """
    notes: list[str] = []
    if not historical_records:
        return CrossCheckResult(
            sample_size=0,
            median_60d_return=None,
            median_drawdown=None,
            notes=["no historical records available (DB not wired or empty)"],
        )

    industry_prefix = (industry_code or "")[:4]
    matches = [
        r
        for r in historical_records
        if r.get("listing_type") == listing_type.value
        and (r.get("industry_code") or "").startswith(industry_prefix)
    ]
    if not matches:
        notes.append(
            f"no historical IPO matched listing_type={listing_type.value}, "
            f"industry_prefix={industry_prefix!r}"
        )
        return CrossCheckResult(
            sample_size=0,
            median_60d_return=None,
            median_drawdown=None,
            notes=notes,
        )

    rets = [float(r["return_60d"]) for r in matches if r.get("return_60d") is not None]
    dds = [float(r["max_drawdown_60d"]) for r in matches if r.get("max_drawdown_60d") is not None]

    import statistics  # noqa: PLC0415 — local for one-off use

    return CrossCheckResult(
        sample_size=len(matches),
        median_60d_return=statistics.median(rets) if rets else None,
        median_drawdown=statistics.median(dds) if dds else None,
        notes=notes,
    )


__all__ = ("CrossCheckResult", "cross_check")
