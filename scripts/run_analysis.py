"""CLI: run full analysis for an IPO.

Per PROJECT_SPEC.md Phase 6 DONE condition:
    ``scripts/run_analysis.py --ipo SAMPLE`` 端到端跑通，输出完整投决备忘录。

Usage:
    uv run python scripts/run_analysis.py --ipo SAMPLE
    uv run python scripts/run_analysis.py --ipo "某AI公司" --listing-type MAINBOARD_TECH
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

# Ensure src is importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.common.llm_client import LLMClient
from hk_ipo_agent.common.schemas import (
    Citation,
    FinancialSnapshot,
    ProspectusExtraction,
)
from hk_ipo_agent.orchestrator.graph import build_main_graph
from hk_ipo_agent.orchestrator.states import AnalysisState
from hk_ipo_agent.prediction_registry.registry import get_registry, reset_registry
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples


def _sample_extraction(ipo_name: str, listing_type: ListingType) -> ProspectusExtraction:
    """Build a synthetic ProspectusExtraction for demo/testing.

    In production, this comes from Phase 3 prospectus pipeline.
    """
    return ProspectusExtraction(
        prospectus_id=f"P-CLI-{ipo_name}",
        company_name_zh=ipo_name,
        listing_type=listing_type,
        industry_code="AI",
        industry_description="AI / SaaS / 人工智能",
        business_model="B2B AI subscription platform",
        financials=[
            FinancialSnapshot(
                fiscal_year=2024,
                fiscal_period="FY",
                revenue_rmb=Decimal("500000000"),
                net_profit_rmb=Decimal("50000000"),
                gross_margin=0.45,
                citation=Citation(page=42),
            ),
            FinancialSnapshot(
                fiscal_year=2025,
                fiscal_period="FY",
                revenue_rmb=Decimal("800000000"),
                net_profit_rmb=Decimal("80000000"),
                gross_margin=0.50,
                cash_balance_rmb=Decimal("200000000"),
                citation=Citation(page=43),
            ),
        ],
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )


def _sample_market_data(listing_type: ListingType) -> MarketData:
    """Build synthetic MarketData for demo/testing."""
    return MarketData(
        as_of_date=date.today(),
        listing_type=listing_type,
        peer_multiples=PeerMultiples(
            ps_ttm=[4.0, 6.0, 8.0, 10.0, 12.0],
            pe_ttm=[20.0, 25.0, 30.0, 35.0],
            sample_size=5,
        ),
        regime_score=0.05,
        extra={"mc_seed": 42},
    )


async def run(ipo_name: str, listing_type: ListingType) -> None:
    """Execute the full orchestration pipeline."""
    reset_registry()

    llm_client = LLMClient()
    extraction = _sample_extraction(ipo_name, listing_type)
    market_data = _sample_market_data(listing_type)

    extras = WorkflowExtras(
        pricing_date=date.today(),
        cornerstone_profiles=[
            {"name": "国投1号", "category": "sovereign", "ultimate_holder": "国投"},
            {"name": "国投2号", "category": "sovereign", "ultimate_holder": "国投"},
            {"name": "中信1号", "category": "strategic", "ultimate_holder": "中信"},
        ],
        sponsor_track_records=[
            {"name": "中金", "win_rate_24m": 0.72, "sample_size_24m": 18},
        ],
        peer_multiples={"ps_ttm": [4.0, 6.0, 8.0, 10.0, 12.0], "pe_ttm": [20.0, 25.0, 30.0]},
    )

    initial: AnalysisState = {
        "ipo_id": f"ipo-cli-{ipo_name}",
        "prospectus_id": extraction.prospectus_id,
        "as_of_date": date.today(),
        "extraction": extraction,
        "extras": extras,
    }

    print(f"[run_analysis] Starting full analysis for: {ipo_name}")
    print(f"[run_analysis] Listing type: {listing_type.value}")
    print(f"[run_analysis] As of: {date.today()}")
    print("-" * 60)

    graph = build_main_graph(
        llm_client=llm_client,
        market_data=market_data,
        use_checkpointer=True,
    )

    final = await graph.ainvoke(initial)

    # Output summary.
    print("\n" + "=" * 60)
    print("[RESULT] Analysis complete.")
    print(f"  Agents ran: {len(final.get('agent_outputs', {}))}")
    print(f"  Debate rounds: {len(final['debate_output'].rounds)}")
    print(f"  Decision: {final['decision'].decision.value}")
    print(f"  Confidence: {final['decision'].confidence:.2f}")
    print(f"  Allocation: {final['decision'].suggested_allocation_pct}")
    print(f"  Snapshot ID: {final['snapshot_id']}")
    print(f"  NACS regime_score: {final['extras'].regime_score}")
    print(f"  NACS cluster_bonus: {final['extras'].cluster_bonus_multiplier}")
    print(f"  NACS theme_heat: {final['extras'].theme_heat}")
    print(f"  NACS ai_gilding: {final['extras'].ai_gilding_flag}")
    print("=" * 60)

    # Price range.
    d = final["decision"]
    print("\n[PRICE RANGE]")
    print(f"  Low:  {d.price_range_low}")
    print(f"  Fair: {d.price_range_fair}")
    print(f"  High: {d.price_range_high}")

    # Scorecard.
    print("\n[SCORECARD]")
    for k, v in d.scorecard.items():
        print(f"  {k}: {v:.1f}")

    # Key reasons.
    print("\n[REASONS FOR]")
    for r in d.key_reasons_for:
        print(f"  + {r}")
    print("\n[REASONS AGAINST]")
    for r in d.key_reasons_against:
        print(f"  - {r}")

    # Trigger rules.
    print(f"\n[TRIGGER RULES] ({len(d.trigger_rules)} rules)")
    for rule in d.trigger_rules:
        print(f"  [{rule.trigger_type}] {rule.description}")

    # Verify snapshot integrity.
    reg = get_registry()
    fetched = await reg.get_snapshot(final["snapshot_id"])
    print(f"\n[SNAPSHOT] Integrity verified: {fetched.id}")

    # Total cost.
    print(f"\n[COST] Total LLM cost: ${llm_client.cost_log.total_usd():.4f}")
    print("[DONE] Full memo output would go to reports/ in Phase 7.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full IPO analysis pipeline.")
    parser.add_argument(
        "--ipo",
        required=True,
        help="IPO identifier or company name (e.g., 'SAMPLE', '某AI公司')",
    )
    parser.add_argument(
        "--listing-type",
        default="MAINBOARD_TECH",
        help="Listing type enum value (default: MAINBOARD_TECH)",
    )
    args = parser.parse_args()

    try:
        lt = ListingType(args.listing_type)
    except ValueError:
        valid = [e.value for e in ListingType]
        print(f"Error: invalid listing type '{args.listing_type}'. Valid: {valid}")
        sys.exit(1)

    asyncio.run(run(args.ipo, lt))


if __name__ == "__main__":
    main()
