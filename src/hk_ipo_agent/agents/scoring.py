"""ScoreCard Pydantic schema helpers per ADR 0009.

Each agent emits a domain-specific ScoreCard (subclass of ``BaseScoreCard``)
where every field is a 0-100 dimension. The orchestrator collects these
into ``WorkflowExtras.misc['score_cards']`` for the synthesizer (Phase 6).

Pattern borrowed from
``D:/自定义工具/港股数据分析/港股基石建模/港股研究agent/src/feedback/models.py``
+ ``src/agents/scoring.py`` but adapted to spec §7 (strict Pydantic + citation).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..common.schemas import Citation


class BaseScoreCard(BaseModel):
    """All agent score cards inherit this. Each subclass declares its
    own 0-100 score fields. ``evidence_pages`` and ``notes`` are always
    available."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    evidence_pages: list[int] = Field(default_factory=list)
    notes: str = ""

    def score_dict(self) -> dict[str, float]:
        """Project to ``dict[str, float]`` for ``AgentOutput.scores``.

        Excludes the helper fields (``evidence_pages``, ``notes``).
        """
        excluded = {"evidence_pages", "notes"}
        return {
            k: float(v)
            for k, v in self.model_dump().items()
            if k not in excluded and isinstance(v, (int, float))
        }

    def overall(self) -> float:
        """Equal-weighted average of all numeric score fields."""
        scores = self.score_dict()
        if not scores:
            return 0.0
        return float(sum(scores.values()) / len(scores))


# ---------------------------------------------------------------------------
# JSON block extraction (helpers for LLM output parsing).
# ---------------------------------------------------------------------------

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_block(text: str) -> dict[str, Any] | None:
    """Find the first ```json``` fenced block in text and parse it.

    Returns ``None`` if no block / parse fails.
    """
    m = _FENCED_JSON_RE.search(text)
    if not m:
        return None
    try:
        result = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def strip_json_blocks(text: str) -> str:
    """Remove ```json``` fences from text (for human-readable report)."""
    return _FENCED_JSON_RE.sub("", text).strip()


def schema_instruction(card_cls: type[BaseScoreCard]) -> str:
    """Generate the ``# Output Schema`` block to append to a prompt."""
    fields_doc: list[str] = []
    for name, info in card_cls.model_fields.items():
        if name in {"evidence_pages", "notes"}:
            continue
        ann = info.annotation
        default = info.default if info.default is not None else "required"
        fields_doc.append(f"- `{name}` ({ann}): {info.description or ''} (default: {default})")
    body = "\n".join(fields_doc)
    return f"""

# Output Schema (ScoreCard)

After your analysis, emit a fenced ```json``` block with a ScoreCard:

Fields (all 0-100 unless noted):
{body}

Plus mandatory:
- `evidence_pages` (list[int]): the prospectus page numbers your scores cite
- `notes` (str): one-sentence summary of the score rationale

Example format:
```json
{{
  "score_field_1": 75.0,
  "score_field_2": 60.0,
  "evidence_pages": [12, 42],
  "notes": "Strong growth, moderate margin"
}}
```
"""


# ---------------------------------------------------------------------------
# Per-agent ScoreCards
# ---------------------------------------------------------------------------


class FundamentalScoreCard(BaseScoreCard):
    business_quality: float = Field(ge=0, le=100, description="Business model durability + moat")
    financial_health: float = Field(ge=0, le=100, description="Margin / cash / leverage")
    governance: float = Field(
        ge=0, le=100, description="Board / controlling shareholders / structure"
    )


class IndustryScoreCard(BaseScoreCard):
    competitive_position: float = Field(ge=0, le=100, description="HHI / rank / share")
    growth_outlook: float = Field(ge=0, le=100, description="TAM CAGR / penetration")
    comp_valuation: float = Field(ge=0, le=100, description="Cheap / fair / rich vs peers")


class ValuationScoreCard(BaseScoreCard):
    """Valuation agent emits its own scores in addition to delegating to
    ``valuation/ensemble.py``. These reflect *appropriateness* of the
    valuation approach, not the price level itself."""

    method_fit: float = Field(
        ge=0, le=100, description="How well chosen methods fit this listing type"
    )
    assumption_quality: float = Field(
        ge=0, le=100, description="How defensible are key MC assumptions"
    )
    upside_downside_ratio: float = Field(
        ge=0, le=100, description="P75/P25 ratio normalized (1.0x = 50)"
    )


class PolicyScoreCard(BaseScoreCard):
    regime_fit: float = Field(ge=0, le=100, description="Listing type matches favorable regime")
    policy_tailwind: float = Field(
        ge=0, le=100, description="Industry-specific subsidy / strategic positioning"
    )
    regime_score: float = Field(
        ge=-100,
        le=100,
        description=(
            "NACS Regime Gate: median 30d return % of HK IPOs in [pricing-120d, "
            "pricing-30d]; negative → ensemble forces SKIP (ADR 0005 §2)"
        ),
    )


class LiquidityScoreCard(BaseScoreCard):
    float_quality: float = Field(ge=0, le=100, description="Free-float ratio + concentration")
    lockup_risk: float = Field(
        ge=0, le=100, description="6m post-IPO lockup expiry pressure (lower=better)"
    )
    southbound_eligibility: float = Field(
        ge=0, le=100, description="Stock Connect inclusion likelihood"
    )


class CornerstoneScoreCard(BaseScoreCard):
    sponsor_quality: float = Field(ge=0, le=100, description="Sponsor 24m HK IPO track record")
    cornerstone_strength: float = Field(
        ge=0, le=100, description="Roster quality (sovereign / strategic / hedge)"
    )
    cluster_bonus: float = Field(
        ge=0,
        le=100,
        description=(
            "NACS Cluster Bonus: ≥2 cornerstones share ultimate_holder → bonus "
            "(ADR 0005 §2). 0=none, 50=one cluster, 100=multi-cluster"
        ),
    )


class SentimentScoreCard(BaseScoreCard):
    market_temperature: float = Field(ge=0, le=100, description="HK IPO market warmth proxy")
    narrative_risk: float = Field(ge=0, le=100, description="Story coherence + AI gilding flag")
    theme_heat: float = Field(ge=0, le=100, description="NACS theme heat 0-100 (ADR 0005 §5)")


__all__ = (
    "BaseScoreCard",
    "Citation",
    "CornerstoneScoreCard",
    "FundamentalScoreCard",
    "IndustryScoreCard",
    "LiquidityScoreCard",
    "PolicyScoreCard",
    "SentimentScoreCard",
    "ValuationScoreCard",
    "extract_json_block",
    "schema_instruction",
    "strip_json_blocks",
)
