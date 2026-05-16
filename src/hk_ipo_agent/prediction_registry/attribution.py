"""Per-sample 3-layer attribution engine per PROJECT_SPEC.md §3.11.

Given (snapshot, outcome) at a review checkpoint, produces an
``Attribution`` Pydantic blob covering:

1. **Agent layer**: for each agent_role in the snapshot, score
   calibration (did high score correlate with positive return?) +
   findings accuracy (Bear-style risk calls validated by outcome)
2. **Valuation layer**: per-model deviation of actual price vs P50,
   plus whether the actual close landed in P10-P90
3. **Debate quality**: Bear / Bull prediction validation rates +
   surfaces critical risks that the Synthesizer ignored but were
   later validated

The Opus diagnosis is a short markdown blob (≤500 words) that ties
the three layers together and proposes adjustments (``ProposedAdjustment``
list, status=proposed) for the Phase 10 learning loop.

This engine is deterministic up to the LLM call — given the same
inputs and a seeded mock LLM, the same Attribution comes out. That
makes the unit-test surface tractable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from ..common.enums import AdjustmentType, Confidence
from ..common.llm_client import LLMClient
from ..common.logging import get_logger
from ..common.schemas import (
    AgentErrorAnalysis,
    Attribution,
    DebateQualityAnalysis,
    PredictionOutcome,
    PredictionSnapshot,
    ProposedAdjustment,
    ValuationErrorAnalysis,
)

logger = get_logger(__name__)

DIAGNOSIS_MODEL = "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Pydantic shapes the LLM is asked to fill
# ---------------------------------------------------------------------------


class _ProposedAdjustmentLLM(BaseModel):
    """LLM-facing slim version of ProposedAdjustment — we add IDs after."""

    target_path: str
    adjustment_type: AdjustmentType
    current_value: Any
    proposed_value: Any
    rationale: str = Field(..., max_length=500)
    expected_impact: str = Field(..., max_length=200)
    confidence: Confidence


class _DiagnosisOutput(BaseModel):
    """Pydantic-validated synthesis from Opus."""

    primary_attribution: str = Field(..., max_length=80)
    llm_diagnosis: str = Field(..., max_length=2000)
    proposed_adjustments: list[_ProposedAdjustmentLLM] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Attribution engine
# ---------------------------------------------------------------------------


class AttributionEngine:
    """Per-sample attribution. Stateless apart from the injected LLM."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        diagnosis_model: str = DIAGNOSIS_MODEL,
    ) -> None:
        self._llm = llm
        self._model = diagnosis_model

    async def attribute(
        self,
        *,
        snapshot: PredictionSnapshot,
        outcome: PredictionOutcome,
        actual_price: Decimal,
    ) -> Attribution:
        """Build a full Attribution for ``(snapshot, outcome)`` at ``actual_price``."""

        # 1. Agent layer
        agent_errors = self._build_agent_errors(snapshot, outcome)
        # 2. Valuation layer
        valuation_errors = self._build_valuation_errors(snapshot, actual_price)
        # 3. Debate quality
        debate_quality = self._build_debate_quality(snapshot, outcome)

        # 4. Opus synthesis
        diagnosis = await self._diagnose(
            snapshot=snapshot,
            outcome=outcome,
            agent_errors=agent_errors,
            valuation_errors=valuation_errors,
            debate_quality=debate_quality,
        )
        proposed_adjustments = [
            ProposedAdjustment(
                target_path=a.target_path,
                adjustment_type=a.adjustment_type,
                current_value=a.current_value,
                proposed_value=a.proposed_value,
                rationale=a.rationale,
                evidence_snapshot_ids=[snapshot.id],
                expected_impact=a.expected_impact,
                confidence=a.confidence,
            )
            for a in diagnosis.proposed_adjustments
        ]

        return Attribution(
            snapshot_id=snapshot.id,
            checkpoint_day=outcome.checkpoint_day,
            agent_errors=agent_errors,
            valuation_errors=valuation_errors,
            debate_quality=debate_quality,
            primary_attribution=diagnosis.primary_attribution,
            llm_diagnosis=diagnosis.llm_diagnosis,
            proposed_adjustments=proposed_adjustments,
        )

    # ------------------------------------------------------------------
    # Layer 1 — agent calibration
    # ------------------------------------------------------------------

    @staticmethod
    def _build_agent_errors(
        snapshot: PredictionSnapshot,
        outcome: PredictionOutcome,
    ) -> list[AgentErrorAnalysis]:
        """Score calibration: high score should track positive realised return.

        ``score_calibration`` ∈ [-1, +1]; positive = miscalibrated optimistic,
        negative = miscalibrated pessimistic, near 0 = well-calibrated.
        """
        ret = outcome.return_since_listing if outcome.return_since_listing is not None else outcome.return_since_ipo
        analyses: list[AgentErrorAnalysis] = []
        for role_value, ao in snapshot.agent_outputs.items():
            score = ao.overall_score / 100.0  # normalise to [0, 1]
            # Miscalibration signal: agent scored 0.8 but stock fell 20% → +1.0 miscalibration
            miscalibration = score - (0.5 + min(max(ret, -0.5), 0.5))
            # Findings accuracy: fraction of HIGH confidence findings that
            # were validated by the outcome direction. Heuristic only —
            # full validation needs LLM in Phase 10.
            high_conf = [f for f in ao.key_findings if f.confidence.value == "high"]
            critical_misses: list[str] = []
            critical_correct: list[str] = []
            for finding in high_conf:
                # Without ground-truth tags we treat negative-toned findings
                # validated when ret < 0, and vice versa.
                negative = any(w in finding.statement for w in ("风险", "下", "下行", "miss", "warning"))
                if (negative and ret < 0) or (not negative and ret > 0):
                    critical_correct.append(finding.statement[:120])
                else:
                    critical_misses.append(finding.statement[:120])
            findings_accuracy = (
                len(critical_correct) / max(len(high_conf), 1) if high_conf else 0.0
            )
            analyses.append(
                AgentErrorAnalysis(
                    agent_role=ao.agent_role,
                    score_calibration=round(miscalibration, 4),
                    findings_accuracy=round(findings_accuracy, 4),
                    critical_misses=critical_misses[:5],
                    critical_correct_calls=critical_correct[:5],
                )
            )
        return analyses

    # ------------------------------------------------------------------
    # Layer 2 — valuation model deviation
    # ------------------------------------------------------------------

    @staticmethod
    def _build_valuation_errors(
        snapshot: PredictionSnapshot,
        actual_price: Decimal,
    ) -> list[ValuationErrorAnalysis]:
        errors: list[ValuationErrorAnalysis] = []
        for model in snapshot.valuation_output.single_models:
            if not model.applicable or model.valuation_distribution is None:
                continue
            dist = model.valuation_distribution
            p50 = dist.p50
            if p50 == 0:
                continue
            pct_err = float((actual_price - p50) / p50)
            in_range = dist.p10 <= actual_price <= dist.p90
            errors.append(
                ValuationErrorAnalysis(
                    model_name=model.model_name,
                    predicted_p50=p50,
                    actual_price=actual_price,
                    pct_error=round(pct_err, 6),
                    in_p10_p90_range=in_range,
                )
            )
        return errors

    # ------------------------------------------------------------------
    # Layer 3 — debate quality
    # ------------------------------------------------------------------

    @staticmethod
    def _build_debate_quality(
        snapshot: PredictionSnapshot,
        outcome: PredictionOutcome,
    ) -> DebateQualityAnalysis:
        """Counts Bear vs Bull predictions that the outcome validated."""
        rounds = snapshot.debate_output.rounds
        ret = outcome.return_since_listing if outcome.return_since_listing is not None else outcome.return_since_ipo
        # Heuristic: bear "wins" if return negative; bull "wins" if positive.
        bear_total = sum(1 for r in rounds if r.bear_argument)
        bull_total = sum(1 for r in rounds if r.bull_argument)
        bear_validated = bear_total if ret < -0.05 else 0
        bull_validated = bull_total if ret > 0.05 else 0
        unaddressed: list[str] = []
        if ret < -0.10:
            unaddressed.extend(r.bear_argument[:120] for r in rounds if "未" in r.resolution or "保留" in r.resolution)
        return DebateQualityAnalysis(
            bear_predictions_validated=bear_validated,
            bear_predictions_total=bear_total,
            bull_predictions_validated=bull_validated,
            bull_predictions_total=bull_total,
            unaddressed_critical_risks=unaddressed[:5],
        )

    # ------------------------------------------------------------------
    # Opus synthesis
    # ------------------------------------------------------------------

    async def _diagnose(
        self,
        *,
        snapshot: PredictionSnapshot,
        outcome: PredictionOutcome,
        agent_errors: list[AgentErrorAnalysis],
        valuation_errors: list[ValuationErrorAnalysis],
        debate_quality: DebateQualityAnalysis,
    ) -> _DiagnosisOutput:
        prompt = _format_diagnosis_prompt(
            snapshot=snapshot,
            outcome=outcome,
            agent_errors=agent_errors,
            valuation_errors=valuation_errors,
            debate_quality=debate_quality,
        )
        try:
            return await self._llm.acomplete_json(
                model=self._model,
                system=_DIAGNOSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                response_model=_DiagnosisOutput,
                agent_role="attribution",
                ipo_id=str(snapshot.ipo_id),
                temperature=0.2,
                max_tokens=2048,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "attribution_diagnosis_failed",
                snapshot_id=str(snapshot.id), error=str(exc),
            )
            # Graceful degrade: return numeric-only summary.
            return _DiagnosisOutput(
                primary_attribution="diagnosis_unavailable",
                llm_diagnosis=(
                    f"LLM diagnosis failed: {exc}. Numeric layer: "
                    f"{len(agent_errors)} agent errors, "
                    f"{len(valuation_errors)} valuation errors, "
                    f"bear {debate_quality.bear_predictions_validated}/"
                    f"{debate_quality.bear_predictions_total} validated."
                ),
                proposed_adjustments=[],
            )


_DIAGNOSIS_SYSTEM_PROMPT = (
    "You are the post-mortem analyst for an HK IPO investment-decision system. "
    "Given a snapshot of the original analysis + the realised outcome at a "
    "checkpoint, produce a concise (≤500 字) Chinese diagnosis: 1) the single "
    "primary attribution (one of: agent_calibration / valuation_model / "
    "debate_blindspot / regime_shift / cornerstone_signal / extraction_quality / "
    "unforeseen_event); 2) a markdown explanation tying the three layers "
    "together; 3) up to 3 proposed adjustments matching the ProposedAdjustment "
    "schema. Be specific about target_path (e.g. 'config/valuation_weights.yaml'). "
    "If the realised outcome is within tolerance, propose no adjustments."
)


def _format_diagnosis_prompt(
    *,
    snapshot: PredictionSnapshot,
    outcome: PredictionOutcome,
    agent_errors: list[AgentErrorAnalysis],
    valuation_errors: list[ValuationErrorAnalysis],
    debate_quality: DebateQualityAnalysis,
) -> str:
    ret_str = f"{(outcome.return_since_listing or outcome.return_since_ipo):.2%}"
    lines = [
        f"# IPO {snapshot.ipo_id} 的 T+{outcome.checkpoint_day} 复盘",
        f"决策: {snapshot.decision.decision.value} (confidence={snapshot.decision.confidence})",
        f"预测价格区间: [{snapshot.decision.price_range_low}, {snapshot.decision.price_range_high}]",
        f"实际收益: {ret_str}; 预测带内: {outcome.price_in_predicted_range}",
        "",
        "## Agent layer",
        *(
            f"- {a.agent_role.value}: miscalib={a.score_calibration}, "
            f"acc={a.findings_accuracy}, misses={len(a.critical_misses)}"
            for a in agent_errors
        ),
        "",
        "## Valuation layer",
        *(
            f"- {v.model_name}: P50={v.predicted_p50}, actual={v.actual_price}, "
            f"err={v.pct_error:.2%}, in P10-P90={v.in_p10_p90_range}"
            for v in valuation_errors
        ),
        "",
        "## Debate quality",
        f"- Bear: {debate_quality.bear_predictions_validated}/{debate_quality.bear_predictions_total} validated",
        f"- Bull: {debate_quality.bull_predictions_validated}/{debate_quality.bull_predictions_total} validated",
        f"- Unaddressed critical risks: {len(debate_quality.unaddressed_critical_risks)}",
        "",
        "输出 JSON：{primary_attribution, llm_diagnosis, proposed_adjustments[]}",
    ]
    return "\n".join(lines)


__all__ = (
    "DIAGNOSIS_MODEL",
    "AttributionEngine",
)
