"""Monitoring trigger rules generator.

Per PROJECT_SPEC.md §6 ``TriggerRule`` + §7. After a decision is made,
emit rules the lifecycle monitor (Phase 7.5) uses to detect when this
decision should be re-evaluated. Examples:

- "price drops > 20% within 30 days" → CRITICAL alert
- "cornerstone discloses sell-down" → MAJOR alert
- "earnings miss vs prospectus by > 15%" → MAJOR alert
"""

from __future__ import annotations

from ..agents.workflow_extras import WorkflowExtras
from ..common.enums import AlertLevel, DecisionType
from ..common.schemas import FinalDecision, TriggerRule, ValuationEnsembleOutput


def build_trigger_rules(
    *,
    decision_type: DecisionType,
    ensemble: ValuationEnsembleOutput,
    extras: WorkflowExtras,
) -> list[TriggerRule]:
    """Build a minimal but useful trigger set for the lifecycle monitor."""
    rules: list[TriggerRule] = [
        TriggerRule(
            condition="post_ipo_price_drop_pct_30d > 20",
            action="emit MAJOR alert; trigger early outcome checkpoint",
            severity=AlertLevel.WARNING,
        ),
        TriggerRule(
            condition="post_ipo_price_drop_pct_30d > 40",
            action="emit CRITICAL alert; force prediction review",
            severity=AlertLevel.CRITICAL,
        ),
        TriggerRule(
            condition="cornerstone_disclosure detected",
            action="re-run cornerstone_signal_agent + ad-hoc snapshot diff",
            severity=AlertLevel.WARNING,
        ),
        TriggerRule(
            condition="earnings_release detected",
            action="run earnings_comparator; flag if deviation_pct > 15",
            severity=AlertLevel.INFO,
        ),
    ]

    # Conditional: AI gilding requires tighter monitoring on revenue mix.
    if extras.ai_gilding_flag:
        rules.append(
            TriggerRule(
                condition="quarterly_report shows AI revenue share < 10%",
                action="confirm AI gilding diagnosis; mark for synthesizer rerun",
                severity=AlertLevel.WARNING,
            )
        )

    # Conditional: negative-regime SKIP needs an "auto-revert" rule.
    if decision_type == DecisionType.SKIP and (
        extras.regime_score is not None and extras.regime_score < 0
    ):
        rules.append(
            TriggerRule(
                condition="regime_score turns >= 0 within 60d of decision",
                action="reopen evaluation pipeline; do not auto-buy",
                severity=AlertLevel.INFO,
            )
        )

    return rules


def attach_trigger_rules(
    decision: FinalDecision,
    rules: list[TriggerRule],
) -> FinalDecision:
    """Return a copy of ``decision`` with ``trigger_rules`` replaced."""
    return decision.model_copy(update={"trigger_rules": rules})


__all__ = ("attach_trigger_rules", "build_trigger_rules")
