"""Tests for orchestrator/graph.py — compile + structural correctness.

Phase 6: validates the LangGraph topology without actually running the
agents (LLM mocking through 7 agents end-to-end is integration-test
territory; here we just check compile + edges).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.orchestrator.edges import (
    route_after_hitl,
    route_after_snapshot,
    route_after_validation,
)
from hk_ipo_agent.orchestrator.graph import build_main_graph
from hk_ipo_agent.valuation.base import MarketData


def _md() -> MarketData:
    return MarketData(
        as_of_date=date(2026, 5, 16),
        listing_type=ListingType.MAINBOARD_TECH,
    )


def test_graph_compiles(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    g = build_main_graph(llm_client=LLMClient(daily_budget_usd=Decimal("100")), market_data=_md())
    nodes = set(g.get_graph().nodes.keys())
    # All 13 expected nodes plus __start__ / __end__.
    expected = {
        "fundamental",
        "industry",
        "policy",
        "liquidity",
        "cornerstone",
        "sentiment",
        "valuation",
        "debate",
        "cross_check",
        "synthesize",
        "create_snapshot",
        "hitl_wait",
        "report",
    }
    assert expected.issubset(nodes)


def test_route_after_snapshot_bypass_when_hitl_off(monkeypatch) -> None:
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "false")
    get_settings.cache_clear()
    assert route_after_snapshot({}) == "report"


def test_route_after_snapshot_to_hitl_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")
    get_settings.cache_clear()
    assert route_after_snapshot({}) == "hitl_wait"
    # already approved → straight to report
    assert route_after_snapshot({"hitl_status": "approved"}) == "report"
    # cleanup
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "false")
    get_settings.cache_clear()


def test_route_after_hitl_states() -> None:
    assert route_after_hitl({"hitl_status": "approved"}) == "report"
    assert route_after_hitl({"hitl_status": "rejected"}) == "END"
    assert route_after_hitl({"hitl_status": "pending"}) == "hitl_wait"


def test_route_after_validation_default() -> None:
    assert route_after_validation({}) == "parallel_agents"
