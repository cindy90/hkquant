"""Investment-memo builder per PROJECT_SPEC.md §3.13 / §7.

Takes a ``PredictionSnapshot`` (the immutable output of Phase 6
``create_snapshot``) and produces a markdown document via Jinja2.

The PDF / DOCX exporters consume the markdown produced here.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..agents.workflow_extras import WorkflowExtras
from ..common.schemas import PredictionSnapshot

_TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"

_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(disabled_extensions=("j2",), default_for_string=False),
    trim_blocks=False,
    lstrip_blocks=False,
)


def _interpret_regime(score: float | None) -> str:
    """Human-readable regime gate interpretation."""
    if score is None:
        return "数据缺失，未触发硬门"
    if score < 0:
        return "<0 — Regime Gate 已触发，估值集合强制 SKIP"
    if score < 0.05:
        return "borderline，价格区间 ±10% 加宽"
    return ">=0 — 市场环境支持"


def asdict_safe(obj: object) -> dict[str, Any]:
    """Convert Pydantic / dataclass / dict to dict for Jinja consumption."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")  # type: ignore[no-any-return]
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(cast(Any, obj))
    if isinstance(obj, dict):
        return obj
    return {"value": str(obj)}


def _ctx_from_snapshot(snapshot: PredictionSnapshot) -> dict[str, Any]:
    """Flatten a snapshot into the template variables expected by the memo."""
    extraction = snapshot.input_data_snapshot.get("extraction", {})
    decision = snapshot.decision
    ensemble = snapshot.valuation_output
    debate = snapshot.debate_output

    raw_extras = snapshot.input_data_snapshot.get("extras")
    if isinstance(raw_extras, dict):
        extras = WorkflowExtras(
            **{k: v for k, v in raw_extras.items() if k != "misc"},
            misc=raw_extras.get("misc", {}),
        )
    elif isinstance(raw_extras, WorkflowExtras):
        extras = raw_extras
    else:
        extras = WorkflowExtras()

    return {
        "company_name_zh": extraction.get("company_name_zh", ""),
        "company_name_en": extraction.get("company_name_en"),
        "stock_code": extraction.get("stock_code"),
        "listing_type": extraction.get("listing_type"),
        "industry_code": extraction.get("industry_code"),
        "industry_description": extraction.get("industry_description"),
        "as_of_date": snapshot.as_of_date.isoformat(),
        "snapshot_id": str(snapshot.id),
        "system_version": snapshot.system_version,
        # Decision
        "decision_type": decision.decision.value,
        "confidence_pct": round(decision.confidence * 100, 1),
        "allocation_pct": (
            round(decision.suggested_allocation_pct * 100, 2)
            if decision.suggested_allocation_pct
            else None
        ),
        "price_range": {
            "low": decision.price_range_low,
            "fair": decision.price_range_fair,
            "high": decision.price_range_high,
        },
        "scorecard": decision.scorecard,
        "key_reasons_for": decision.key_reasons_for,
        "key_reasons_against": decision.key_reasons_against,
        "trigger_rules": [asdict_safe(r) for r in decision.trigger_rules],
        # NACS
        "regime_score": extras.regime_score,
        "regime_interpretation": _interpret_regime(extras.regime_score),
        "cluster_bonus_multiplier": extras.cluster_bonus_multiplier,
        "cluster_groups": extras.cluster_groups,
        "theme_heat": extras.theme_heat,
        "theme_matched": extras.theme_matched,
        "ai_gilding_flag": extras.ai_gilding_flag,
        # Agents
        "agent_overalls": {
            role: round(out.overall_score, 1) for role, out in snapshot.agent_outputs.items()
        },
        # Valuation
        "applicable_model_names": [m.model_name for m in ensemble.single_models if m.applicable],
        "weights_used": ensemble.weights_used,
        "ens_p25": ensemble.ensemble_distribution.p25,
        "ens_p50": ensemble.ensemble_distribution.p50,
        "ens_p75": ensemble.ensemble_distribution.p75,
        "ensemble_notes": ensemble.notes,
        # Debate
        "debate_rounds_count": len(debate.rounds),
        "debate_consensus": debate.final_consensus,
        "unresolved_issues": debate.unresolved_issues,
        # Cross-check
        "cross_check_notes": snapshot.config_snapshot.get("cross_check_notes", []),
        # Metadata
        "generated_at": datetime.now(UTC).isoformat(),
        "total_cost_usd": snapshot.total_cost_usd,
        "runtime_seconds": round(snapshot.runtime_seconds, 2),
    }


def build_memo_markdown(snapshot: PredictionSnapshot) -> str:
    """Render the investment memo markdown for a snapshot."""
    template = _ENV.get_template("investment_memo.md.j2")
    return template.render(**_ctx_from_snapshot(snapshot))


__all__ = ("asdict_safe", "build_memo_markdown")


_ = Decimal  # type marker
