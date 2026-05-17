"""matplotlib chart helpers for reports.

Phase 7: minimal set used by the investment memo PDF — valuation
distribution histogram + per-agent score bar chart. All charts return
PNG bytes so callers (PDF / DOCX exporters) embed without intermediate files.
"""

from __future__ import annotations

import io
from decimal import Decimal

import matplotlib
import numpy as np

matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt

from ..common.schemas import AgentOutput, ValuationEnsembleOutput


def valuation_distribution_chart(ensemble: ValuationEnsembleOutput) -> bytes:
    """Bar chart of P10/P25/P50/P75/P90 — the 5-point ensemble percentile band."""
    d = ensemble.ensemble_distribution
    pcts = ["P10", "P25", "P50", "P75", "P90"]
    vals = [float(d.p10), float(d.p25), float(d.p50), float(d.p75), float(d.p90)]

    fig, ax = plt.subplots(figsize=(6.0, 3.2))
    ax.bar(pcts, vals, color="#4C78A8")
    ax.set_title("Ensemble Valuation Distribution (RMB)")
    ax.set_ylabel("Value")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


def agent_scorecard_chart(agent_outputs: dict[str, AgentOutput]) -> bytes:
    """Horizontal bar chart of 7 agent overall scores (0-100)."""
    roles = list(agent_outputs.keys())
    scores = [agent_outputs[r].overall_score for r in roles]

    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    y = np.arange(len(roles))
    ax.barh(y, scores, color="#54A24B")
    ax.set_yticks(y)
    ax.set_yticklabels(roles)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Overall score (0-100)")
    ax.set_title("Agent Scorecards")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


def price_range_chart(low: Decimal, fair: Decimal, high: Decimal) -> bytes:
    """Simple horizontal range chart with low/fair/high markers."""
    fig, ax = plt.subplots(figsize=(6.0, 1.6))
    lo = float(low)
    fa = float(fair)
    hi = float(high)
    if hi <= lo:
        hi = lo + 1.0  # avoid zero range
    ax.hlines(1, lo, hi, colors="#888", linewidth=3)
    for x, label, color in [
        (lo, "low", "#E63946"),
        (fa, "fair", "#1D3557"),
        (hi, "high", "#2A9D8F"),
    ]:
        ax.plot(x, 1, "o", color=color, markersize=10)
        ax.annotate(
            f"{label}\n{x:.0f}", (x, 1), textcoords="offset points", xytext=(0, 12), ha="center"
        )
    ax.set_yticks([])
    ax.set_xlim(lo - (hi - lo) * 0.1, hi + (hi - lo) * 0.1)
    ax.set_title("Implied Price Range (RMB)")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


__all__ = (
    "agent_scorecard_chart",
    "price_range_chart",
    "valuation_distribution_chart",
)
