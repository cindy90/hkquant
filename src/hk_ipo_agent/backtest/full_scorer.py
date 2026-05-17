"""FullPipelineScorer — Phase 9b per ADR 0014.

Wraps the production LangGraph main-graph (``orchestrator/graph.py``)
behind the ``BacktestScorer`` Protocol so the same walk-forward harness
can run either the cheap V8LiteScorer (Phase 8c default) or the full
multi-agent pipeline (this module) at the cost of LLM calls.

Use cases:
- **Phase 9 case studies** on 3-5 listed companies (晶泰 / 黑芝麻 /
  越疆 / 宁德 H / 地平线机器人) — small enough sample that ~$25
  total LLM spend is acceptable.
- **Phase 10 learning loop** spot checks: re-running a subset through
  the full pipeline after a calibration change.

NOT for the routine 50+ sample regression runs (those use V8LiteScorer
via ``scripts/run_backtest.py`` — see ADR 0013 §8c on the cost trade-off).

Design:

1. Caller provides ``LLMClient`` + optional tool factories (prospectus /
   ifind / kb). For tests, pass a mock LLMClient and skip the tools.

2. ``score(provider, sample_input)`` builds a ``MarketData`` from the
   provider's view + sample input, then constructs / loads a
   ``ProspectusExtraction`` (Phase 3 output). For backtest mode this
   typically comes from a fixture or PG ``prospectus_docs`` query.

3. Invokes ``build_main_graph(...).ainvoke(state)`` with a 30-minute
   wall-clock timeout (PROJECT_SPEC.md §13 SLO). On timeout we return
   ``SKIP`` rather than crashing the run.

4. Projects ``state["decision"]`` into ``ScoreOutput.decision_score`` —
   we use ``FinalDecision.confidence`` × {PARTICIPATE: +1, PARTIAL:
   +0.5, SKIP: -1, WAIT: 0} so the score remains monotone with the
   recommendation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ..common.enums import DecisionType, ListingType, RegulatoryRegime
from ..common.exceptions import LookAheadError
from ..common.llm_client import LLMClient
from ..common.logging import get_logger
from ..common.schemas import ProspectusExtraction
from ..valuation.base import MarketData
from .as_of_data import AsOfDataProvider
from .regime_detection import (
    regime_score_from_cache,
    regulatory_regime_for,
)
from .runner import BacktestInput, ScoreOutput

logger = get_logger(__name__)

# PROJECT_SPEC.md §13 wall-clock SLO.
DEFAULT_TIMEOUT_SECONDS: float = 30 * 60  # 30 minutes


# Decision-to-magnitude lookup. Combined with confidence we get a
# rank-IC-friendly continuous score.
_DECISION_DIRECTION: dict[DecisionType, float] = {
    DecisionType.PARTICIPATE: +1.0,
    DecisionType.PARTIAL: +0.5,
    DecisionType.WAIT_FOR_SIGNAL: 0.0,
    DecisionType.SKIP: -1.0,
}


ExtractionFetcher = Callable[
    [BacktestInput, AsOfDataProvider],
    Awaitable[ProspectusExtraction],
]
"""``(sample, provider) → ProspectusExtraction``.

Caller supplies this. In production it queries ``prospectus_docs`` +
the Phase 3 extractor; in tests it returns a fixture extraction.
"""


@dataclass(frozen=True)
class FullScorerConfig:
    """Knobs for the full-pipeline path."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    skip_on_timeout: bool = True
    # When True (default), use the regime_score_from_cache NACS fixture.
    # When False, the caller is expected to inject a live regime score
    # via MarketData.regime_score.
    use_cache_regime: bool = True


class FullPipelineScorer:
    """LangGraph-backed BacktestScorer.

    The scorer is intentionally small — it composes the orchestrator
    rather than re-implementing it. All pipeline behavior changes flow
    through ``orchestrator/graph.py``.
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        extraction_fetcher: ExtractionFetcher,
        prospectus_tool: Any = None,
        ifind_tool: Any = None,
        kb_tool: Any = None,
        config: FullScorerConfig | None = None,
    ) -> None:
        self._llm = llm_client
        self._fetch_extraction = extraction_fetcher
        self._prospectus_tool = prospectus_tool
        self._ifind_tool = ifind_tool
        self._kb_tool = kb_tool
        self._cfg = config or FullScorerConfig()

    async def score(
        self,
        provider: AsOfDataProvider,
        sample_input: BacktestInput,
    ) -> ScoreOutput:
        as_of = provider.as_of_date
        regulatory = regulatory_regime_for(as_of)
        regime = (
            regime_score_from_cache(as_of)
            if self._cfg.use_cache_regime
            else 0.0
        )
        market = MarketData(
            as_of_date=as_of,
            listing_type=sample_input.listing_type or ListingType.MAINBOARD_OTHER,
            regime_score=regime,
        )

        try:
            extraction = await self._fetch_extraction(sample_input, provider)
        except LookAheadError as exc:
            # Caller's fetcher tried to read post-as_of data → surface
            # as SKIP rather than crash the backtest run.
            return ScoreOutput(
                decision_score=-1.0,
                regime_score=regime,
                regulatory_regime=regulatory,
                listing_type=sample_input.listing_type,
                notes=(f"extraction fetcher LookAheadError: {exc}",),
            )

        # Lazy-import the graph so unit tests that don't exercise the
        # full pipeline don't pay the LangGraph import cost.
        from ..orchestrator.graph import build_main_graph  # noqa: PLC0415

        graph = build_main_graph(
            llm_client=self._llm,
            market_data=market,
            prospectus_tool=self._prospectus_tool,
            ifind_tool=self._ifind_tool,
            kb_tool=self._kb_tool,
            use_checkpointer=False,  # walk-forward doesn't need resume
        )
        state: dict[str, Any] = {
            "ipo_id": str(sample_input.ipo_id),
            "prospectus_id": extraction.prospectus_id,
            "as_of_date": as_of,
            "extraction": extraction,
        }

        notes: list[str] = []
        try:
            final_state = await asyncio.wait_for(
                graph.ainvoke(state),
                timeout=self._cfg.timeout_seconds,
            )
        except TimeoutError:
            if not self._cfg.skip_on_timeout:
                raise
            logger.warning(
                "full_pipeline_timeout",
                ipo_id=str(sample_input.ipo_id),
                timeout_s=self._cfg.timeout_seconds,
            )
            return ScoreOutput(
                decision_score=-1.0,
                regime_score=regime,
                regulatory_regime=regulatory,
                listing_type=sample_input.listing_type,
                notes=(
                    f"pipeline timed out after {self._cfg.timeout_seconds}s; "
                    "forced SKIP (ADR 0014 §9b SLO)",
                ),
            )

        decision = final_state.get("decision")
        decision_score = _project_decision(decision, notes)
        return ScoreOutput(
            decision_score=decision_score,
            regime_score=regime,
            regulatory_regime=regulatory,
            listing_type=sample_input.listing_type,
            notes=tuple(notes),
        )


def _project_decision(decision: Any, notes: list[str]) -> float:
    """Project ``FinalDecision`` → continuous decision_score.

    Score = direction * confidence, where direction is in
    {-1, 0, +0.5, +1}. Yields a rank-IC-friendly monotone score:
    higher = more bullish.
    """
    if decision is None:
        notes.append("no decision produced by pipeline; defaulting to 0.0")
        return 0.0
    try:
        kind = DecisionType(decision.decision)
    except (AttributeError, ValueError):
        notes.append(f"unrecognized decision shape: {type(decision).__name__}")
        return 0.0
    direction = _DECISION_DIRECTION.get(kind, 0.0)
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    return direction * confidence


# ===========================================================================
# Helper: a no-op extraction fetcher for unit tests
# ===========================================================================


def make_fixture_extraction_fetcher(
    extraction: ProspectusExtraction,
) -> ExtractionFetcher:
    """Return an ExtractionFetcher that always yields ``extraction``.

    Convenience for unit tests + integration smoke. Production builds
    a fetcher that queries PG ``prospectus_docs`` + the Phase 3 extractor.
    """

    async def _fetcher(
        sample_input: BacktestInput,
        provider: AsOfDataProvider,
    ) -> ProspectusExtraction:
        _ = sample_input, provider  # unused
        return extraction

    return _fetcher


__all__ = (
    "DEFAULT_TIMEOUT_SECONDS",
    "ExtractionFetcher",
    "FullPipelineScorer",
    "FullScorerConfig",
    "make_fixture_extraction_fetcher",
)

# Suppress unused-import warnings (types used in annotations / docstrings).
_ = (date, datetime, UTC, RegulatoryRegime)
