"""CLI: analyse a HK IPO prospectus PDF end-to-end.

Replaces the hard-coded ``scripts/run_e2e_test.py`` (ADR 0016 §Decision
third class). Closes the recurrence pattern where every new IPO produced
a copy-pasted one-off script (which is exactly how the now-archived
``evaluate_new_ipo.py`` and ``search_yifei_tech.py`` were born).

Usage:
    uv run python scripts/analyze_pdf.py \\
        --pdf "<path/to/prospectus.pdf>" \\
        --stock-code 6871.HK \\
        --company-name "浙江翼菲智能科技股份有限公司" \\
        --listing-type CH18C_COMMERCIALIZED \\
        --industry-code machinery_robotics

    # Dry-run prints the resolved plan without making any LLM call:
    uv run python scripts/analyze_pdf.py \\
        --pdf tests/fixtures/sample.pdf --stock-code TEST.HK \\
        --company-name "Test Co" --dry-run

Requirements:
    - KIMI_API_KEY in env (or .env at repo root) for real runs.
    - --dry-run requires neither the key nor the PDF to actually parse.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Windows console defaults to CP936 — force UTF-8 so CJK doesn't garble.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Make ``src`` importable when running from repo root.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env", override=False)

from hk_ipo_agent.common.enums import ListingType  # noqa: E402
from hk_ipo_agent.common.llm_client import LLMClient  # noqa: E402
from hk_ipo_agent.common.settings import clear_config_caches  # noqa: E402
from hk_ipo_agent.pipelines import (  # noqa: E402
    PipelineConfig,
    run_pdf_to_snapshot,
)
from hk_ipo_agent.valuation.base import MarketData, PeerMultiples  # noqa: E402

# Default sane peer multiples for a tech/18C IPO when the caller doesn't
# supply a peer set. These are the same values run_e2e_test.py used for
# 翼菲智能 and produce a non-pathological valuation distribution.
_DEFAULT_PEERS = PeerMultiples(
    pe_ttm=[50.0, 65.0, 80.0, 120.0, 150.0],
    ps_ttm=[8.0, 12.0, 15.0, 20.0, 30.0],
    pb_latest=[3.0, 5.0, 7.0, 10.0, 15.0],
    ev_ebitda=[25.0, 35.0, 45.0, 60.0, 80.0],
    sample_size=5,
)


def _derive_prospectus_id(stock_code: str, company_name_zh: str) -> str:
    """Stable, filename-safe ID. Format: ``<code>-<slug>``."""
    code = stock_code.lower().replace(".", "-")
    slug = re.sub(r"[^a-z0-9]+", "-", company_name_zh.lower()).strip("-")[:40]
    return f"{code}-{slug}" if slug else code


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="analyze_pdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pdf", required=True, type=Path, help="Path to prospectus PDF")
    p.add_argument(
        "--stock-code",
        required=True,
        help="HK ticker, e.g. 6871.HK (will uppercase + ensure .HK suffix)",
    )
    p.add_argument(
        "--company-name",
        required=True,
        help="Company name in Chinese (used by the extractor)",
    )
    p.add_argument(
        "--listing-type",
        default="CH18C_COMMERCIALIZED",
        choices=[lt.name for lt in ListingType],
        help="Listing chapter / type (default: CH18C_COMMERCIALIZED)",
    )
    p.add_argument("--industry-code", default="unknown")
    p.add_argument("--industry-desc", default="")
    p.add_argument("--max-pages", type=int, default=500)
    p.add_argument("--max-chunks-per-section", type=int, default=10)
    p.add_argument("--no-report", action="store_true", help="Skip writing the markdown report")
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument(
        "--budget-usd",
        type=Decimal,
        default=Decimal("10.0"),
        help="LLM daily budget (USD); pipeline aborts on overrun",
    )
    p.add_argument(
        "--regime-score",
        type=float,
        default=0.3,
        help="MarketData regime score (default: 0.3, mildly positive)",
    )
    p.add_argument(
        "--persist",
        action="store_true",
        help="Enable full persistence: extraction→PG, chunks→Qdrant, snapshot→PG",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Force re-extraction even if cached extraction exists in PG",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved plan and exit; no PDF parse, no LLM call",
    )
    return p.parse_args(argv)


def _normalise_stock_code(raw: str) -> str:
    code = raw.strip().upper()
    if not code.endswith(".HK"):
        code += ".HK"
    return code


def _build_config(args: argparse.Namespace) -> PipelineConfig:
    stock_code = _normalise_stock_code(args.stock_code)
    return PipelineConfig(
        pdf_path=args.pdf,
        ipo_id=stock_code,
        prospectus_id=_derive_prospectus_id(stock_code, args.company_name),
        company_name_zh=args.company_name,
        listing_type=ListingType[args.listing_type],
        industry_code=args.industry_code,
        industry_description=args.industry_desc,
        max_pages=args.max_pages,
        max_chunks_per_section=args.max_chunks_per_section,
        persist_to_pg=args.persist,
        persist_to_qdrant=args.persist,
        use_cached_extraction=not getattr(args, "no_cache", False),
        write_report=not args.no_report,
        output_dir=args.out_dir,
    )


def _print_plan(config: PipelineConfig, market_data: MarketData) -> None:
    print("=" * 70)
    print("  analyze_pdf.py — resolved plan")
    print("=" * 70)
    print(f"  PDF path:         {config.pdf_path}")
    print(f"  IPO id:           {config.ipo_id}")
    print(f"  Prospectus id:    {config.prospectus_id}")
    print(f"  Company (zh):     {config.company_name_zh}")
    print(f"  Listing type:     {config.listing_type.name}")
    print(f"  Industry:         {config.industry_code} ({config.industry_description})")
    print(f"  Max pages:        {config.max_pages}")
    print(f"  Max chunks/sec:   {config.max_chunks_per_section}")
    print(f"  Persist to PG:    {config.persist_to_pg}")
    print(f"  Persist to Qdrant: {config.persist_to_qdrant}")
    print(f"  Use cache:        {config.use_cached_extraction}")
    print(f"  Write report:     {config.write_report}")
    print(f"  Output dir:       {config.output_dir or '<default outputs/>'}")
    print("  ---")
    print(f"  Market as_of:     {market_data.as_of_date}")
    print(f"  Regime score:     {market_data.regime_score}")
    print(f"  Risk-free rate:   {market_data.risk_free_rate}")
    print(f"  Equity risk prem: {market_data.equity_risk_premium}")
    print(f"  Peer sample size: {market_data.peer_multiples.sample_size}")


async def _main_async(args: argparse.Namespace) -> int:
    # R5-5: clear config caches ONCE at CLI entry so subsequent
    # ``get_settings()`` / ``load_*_config`` calls re-read YAML + env. The
    # library (run_pdf_to_snapshot) intentionally never touches these
    # caches to keep concurrent invocations isolated.
    clear_config_caches()

    config = _build_config(args)
    market_data = MarketData(
        as_of_date=date.today(),
        listing_type=config.listing_type,
        peer_multiples=_DEFAULT_PEERS,
        regime_score=args.regime_score,
        risk_free_rate=0.025,
        equity_risk_premium=0.07,
    )

    if args.dry_run:
        _print_plan(config, market_data)
        print("\n[dry-run] No PDF parse, no LLM call. Plan above would be executed.")
        return 0

    if not config.pdf_path.exists():
        print(f"[error] PDF not found: {config.pdf_path}", file=sys.stderr)
        return 2

    _print_plan(config, market_data)
    print()

    llm = LLMClient(daily_budget_usd=args.budget_usd)
    result = await run_pdf_to_snapshot(config, market_data, llm_client=llm)

    print()
    print("=" * 70)
    print("  Done")
    print("=" * 70)
    print(f"  Snapshot id:      {result.snapshot_id}")
    print(f"  Total cost (USD): ${result.total_cost_usd:.4f}")
    print(
        f"  Total runtime:    {result.total_elapsed_s:.1f}s ({result.total_elapsed_s / 60:.1f} min)"
    )
    for step, secs in result.step_timings_s.items():
        print(f"    {step:8s}: {secs:6.1f}s")
    if result.report_path:
        print(f"  Report:           {result.report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
