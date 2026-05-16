"""WorkflowExtras — strongly-typed cross-agent shared context.

Per ADR 0009: pattern borrowed from
``D:/自定义工具/港股数据分析/港股基石建模/港股研究agent/src/agents/extras.py``
but extended with NACS signal fields required by ADR 0005 §2 + §5.

Three classes of fields:

1. **NACS signals (ADR 0005)** — must be populated by `policy_agent` /
   `cornerstone_signal_agent` / `sentiment_agent` and consumed by the
   valuation ensemble (`valuation/ensemble.py`) + synthesizer (Phase 6).

2. **Cross-agent market/data state** — peer multiples, comparable IPOs,
   macro indicators. Populated by upstream tools (Phase 2 data layer).

3. **Misc** — typed fallback dict for ad-hoc keys; usage discouraged.

The orchestrator (Phase 6 LangGraph) carries this in `AnalysisState`;
Phase 5 agents read/write it directly via `AgentContext.extras`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class WorkflowExtras:
    """Cross-agent state carrier. Mutable; populated incrementally."""

    # ------------------------------------------------------------------ NACS signals (ADR 0005 §2)
    # Filled by policy_agent. Median 30d return of HK IPOs in
    # [pricing_date - 120d, pricing_date - 30d]. < 0 → Regime Gate triggers
    # the ensemble to force SKIP (valuation/ensemble.py).
    regime_score: float | None = None

    # Filled by cornerstone_signal_agent. ≥1.0 means at least one cluster
    # detected (≥2 cornerstones share same ultimate_holder). Empirically
    # 60d mean +22% (vs +14% baseline), std ↓40%.
    cluster_bonus_multiplier: float | None = None
    cluster_groups: list[dict[str, Any]] = field(default_factory=list)
    """[{ultimate_holder: str, members: [name1, name2], count: int}, ...]"""

    # Filled by sentiment_agent. Theme heat 0.0-1.0 from
    # `data/knowledge_base/themes/heat_today.json`.
    theme_heat: float | None = None
    theme_matched: str | None = None

    # Filled by sentiment_agent. AI revenue share < 10% but claims AI exposure
    # → True; downstream synthesizer applies × 0.85 narrative-risk multiplier.
    ai_gilding_flag: bool = False

    # ------------------------------------------------------------------ Market / data state
    # Comparable peer pool — populated by industry_agent (or upstream tool).
    peer_multiples: dict[str, list[float]] = field(default_factory=dict)
    """{'ps_ttm': [...], 'pe_ttm': [...], 'ev_ebitda': [...]}"""

    # Macro indicators — HSI level / PE, HSTECH level / PE, IPO 60d volume.
    macro_indicators: dict[str, float] = field(default_factory=dict)

    # Contemporaneous IPOs (60d window): used by liquidity / sentiment agents.
    competing_ipos: list[dict[str, Any]] = field(default_factory=list)

    # Sponsor / cornerstone roster snapshot for the target IPO.
    sponsor_track_records: list[dict[str, Any]] = field(default_factory=list)
    cornerstone_profiles: list[dict[str, Any]] = field(default_factory=list)

    # FX + dates for downstream alignment.
    as_of_date: date | None = None
    pricing_date: date | None = None

    # Pre-IPO last-round valuation (RMB) if known from extraction.
    last_round_valuation_rmb: Decimal | None = None

    # ------------------------------------------------------------------ Misc fallback
    misc: dict[str, Any] = field(default_factory=dict)
    """Untyped fallback for ad-hoc keys; prefer adding a typed field above."""

    # ------------------------------------------------------------------ dict-style API
    _RESERVED = ("misc", "_RESERVED")

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style read. Typed fields take precedence over misc."""
        if key in self._RESERVED:
            return default
        if hasattr(self, key):
            return getattr(self, key)
        return self.misc.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Dict-style write. Unknown keys go to misc."""
        if key in self._RESERVED:
            raise KeyError(f"reserved key: {key}")
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            self.misc[key] = value


__all__ = ("WorkflowExtras",)
