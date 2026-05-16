"""Phase 5 multi-agent expert layer — public surface.

7 expert agents + shared base + tools. Agents are constructed once and
their ``async run(ctx) -> AgentOutput`` is called in parallel by the
LangGraph orchestrator (Phase 6).

Example::

    from hk_ipo_agent.agents import (
        AgentContext, BaseAgent,
        FundamentalAgent, IndustryAgent, ValuationAgent,
        PolicyAgent, LiquidityAgent, CornerstoneSignalAgent, SentimentAgent,
    )

    ctx = AgentContext(ipo_id=..., extraction=..., market_data=..., llm_client=...)
    output = await PolicyAgent().run(ctx)
"""

from __future__ import annotations

from .base import AgentContext, BaseAgent, load_prompt
from .cornerstone_signal_agent import CornerstoneSignalAgent, cluster_by_ultimate_holder
from .fundamental_agent import FundamentalAgent
from .industry_agent import IndustryAgent
from .liquidity_agent import LiquidityAgent
from .policy_agent import PolicyAgent, compute_regime_score
from .scoring import (
    BaseScoreCard,
    CornerstoneScoreCard,
    FundamentalScoreCard,
    IndustryScoreCard,
    LiquidityScoreCard,
    PolicyScoreCard,
    SentimentScoreCard,
    ValuationScoreCard,
)
from .sentiment_agent import SentimentAgent, detect_ai_gilding, lookup_ai_revenue
from .valuation_agent import ValuationAgent
from .workflow_extras import WorkflowExtras

__all__ = (
    "AgentContext",
    "BaseAgent",
    "BaseScoreCard",
    "CornerstoneScoreCard",
    "CornerstoneSignalAgent",
    "FundamentalAgent",
    "FundamentalScoreCard",
    "IndustryAgent",
    "IndustryScoreCard",
    "LiquidityAgent",
    "LiquidityScoreCard",
    "PolicyAgent",
    "PolicyScoreCard",
    "SentimentAgent",
    "SentimentScoreCard",
    "ValuationAgent",
    "ValuationScoreCard",
    "WorkflowExtras",
    "cluster_by_ultimate_holder",
    "compute_regime_score",
    "detect_ai_gilding",
    "load_prompt",
    "lookup_ai_revenue",
)
