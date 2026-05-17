"""CLI: migrate NACS v8 SQLite assets to PostgreSQL.

This is the workhorse of Phase 2's NACS legacy inheritance plan
(see ADR 0005 §1 for the complete table mapping). One-shot, idempotent,
required precondition for Phase 2 DONE.

Source:    legacy/data/nacs_real.db (SQLite, 14 tables — Phase 9a archived)
Target:    PostgreSQL schema per PROJECT_SPEC.md §5 (v1.0 ORM only)

Tables migrated (ADR 0005 §1):
- ipo_master           -> ipo_events + ipo_pricings
- ipo_returns          -> ipo_postmarket (scalar columns per ADR 0007;
                         JSONB returns_by_day kept null for NACS data)
- ipo_financials       -> companies + financial_snapshots
- cornerstone_master   -> cornerstone_investors
- cornerstone_aliases  -> cornerstone_investors.aliases JSONB
- ipo_cornerstone_link -> cornerstone_investments (creates investor stubs
                         for orphan cornerstone_id values)
- market_environment_cache -> data/knowledge_base/market_env_cache.json
                              (NOT a PG table; fixture for Phase 8
                              backtest/regime_detection per ADR 0007)

NOT migrated (intentionally):
- cornerstone_performance_asof  -> recomputed in Phase 7.5
- panel_snapshots               -> replaced by prediction_snapshots (v1.1)
- nacs_predictions              -> NACS-specific decision; not relevant in
                                   the new architecture
- price_history                 -> empty in source
- sponsor_performance_asof      -> empty in source
- ipo_industries / ipo_concepts -> stashed onto ipo_events.use_of_proceeds
                                   JSONB (no dedicated v1.0 table)

Stable UUID5 mapping is used everywhere a SQLite TEXT primary key needs to
become a PG uuid; namespace is per-table so cross-table collisions are
impossible:
    pg_uuid = uuid5(NAMESPACE_<TABLE>, sqlite_id_text)

Usage:
    uv run python scripts/migrate_sqlite_to_pg.py             # default
    uv run python scripts/migrate_sqlite_to_pg.py --no-backup # skip backup
    uv run python scripts/migrate_sqlite_to_pg.py --dry-run   # rollback at end
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hk_ipo_agent.common.logging import configure_logging, get_logger
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.database import async_session_factory
from hk_ipo_agent.data.models import (
    Company,
    FinancialSnapshotRow,
    IPOEvent,
)
from hk_ipo_agent.data.repositories import (
    CornerstoneInvestmentRepository,
    CornerstoneInvestorRepository,
    IPOEventRepository,
    IPOPostMarketRepository,
    IPOPricingRepository,
)

log = get_logger("migrate_sqlite_to_pg")

REPO_ROOT = Path(__file__).resolve().parent.parent
# Phase 9a (ADR 0014): NACS SQLite archived to legacy/data/. We try the
# legacy path first, then fall back to the old in-place path for callers
# that haven't been updated.
_LEGACY_SQLITE = REPO_ROOT / "legacy" / "data" / "nacs_real.db"
_INPLACE_SQLITE = REPO_ROOT / "data" / "nacs_real.db"
DEFAULT_SQLITE_PATH = _LEGACY_SQLITE if _LEGACY_SQLITE.exists() else _INPLACE_SQLITE
DEFAULT_FIXTURE_DIR = REPO_ROOT / "data" / "knowledge_base"


# ---------------------------------------------------------------------------
# Deterministic UUID namespaces (per-table) so re-runs produce stable ids.
# ---------------------------------------------------------------------------

NS_IPO = uuid5(NAMESPACE_URL, "hkipo://migration/ipo_master")
NS_CORNERSTONE = uuid5(NAMESPACE_URL, "hkipo://migration/cornerstone_master")
NS_COMPANY = uuid5(NAMESPACE_URL, "hkipo://migration/company")
NS_INVESTMENT = uuid5(NAMESPACE_URL, "hkipo://migration/ipo_cornerstone_link")


def ns_uuid(namespace: UUID, key: str) -> UUID:
    return uuid5(namespace, str(key))


# ---------------------------------------------------------------------------
# Listing-chapter mapping (NACS uses different codes than ListingType enum)
# ---------------------------------------------------------------------------

# NACS listing_chapter -> spec enums.ListingType value (or None to skip)
LISTING_CHAPTER_MAP: dict[str, str] = {
    "18C_pre_commercial": "18C-PRE",
    "18c_pre_commercial": "18C-PRE",
    "18C_commercial": "18C-COMM",
    "18c_commercial": "18C-COMM",
    "18C_commercialized": "18C-COMM",
    "18c_commercialized": "18C-COMM",
    "18C": "18C-COMM",
    "18A": "18A",
    "18a": "18A",
    "AH": "AH",
    "AHa": "AH",
    "AHb": "AH",
    "MB-TECH": "MB-TECH",
    "MB_TECH": "MB-TECH",
    "MB": "MB-OTHER",
    "MAIN": "MB-OTHER",
    "mainboard": "MB-OTHER",
}


def normalize_listing_type(raw: str | None) -> str | None:
    if not raw:
        return None
    key = str(raw).strip()
    if key in LISTING_CHAPTER_MAP:
        return LISTING_CHAPTER_MAP[key]
    # try case-insensitive
    for k, v in LISTING_CHAPTER_MAP.items():
        if k.lower() == key.lower():
            return v
    return "MB-OTHER"  # safe fallback per spec ListingType


# ---------------------------------------------------------------------------
# Backup + open SQLite
# ---------------------------------------------------------------------------


def backup_sqlite(path: Path) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(f".db.bak_{ts}")
    shutil.copy2(path, backup)
    log.info("sqlite_backup_created", source=str(path), backup=str(backup))
    return backup


def open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"SQLite DB not found at {path}")
    # read-only URI so the migration can never write to the source
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------


def to_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (ValueError, ArithmeticError):
        return None


def to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def to_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "y", "t"}
    return None


def _pct_to_ratio(v: Any) -> Decimal | None:
    """NACS stores margins as percent (e.g. 35.5 for 35.5%); convert to ratio."""
    d = to_decimal(v)
    if d is None:
        return None
    return d / Decimal("100")


def to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(v[:10], fmt).date()
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Mappers — SQLite row -> ORM kwargs dict
# ---------------------------------------------------------------------------


def map_ipo_event(row: sqlite3.Row) -> dict[str, Any]:
    listing_type = normalize_listing_type(row["listing_chapter"])
    return {
        "id": ns_uuid(NS_IPO, row["ipo_id"]),
        "stock_code": row["stock_code"],
        "company_name_zh": row["company_name_zh"],
        "company_name_en": row["company_name_en"],
        "listing_type": listing_type,
        "industry_code": row["gics_l2"],
        "sponsor_ids": None,  # Sponsor mapping happens in builder phase
        "a1_filing_date": None,  # NACS doesn't track A1 filing date
        "hearing_date": None,
        "pricing_date": to_date(row["pricing_date"]),
        "listing_date": to_date(row["listing_date"]),
        "issue_size_hkd": to_decimal(row["offering_size_hkd"]),
        "use_of_proceeds": None,
        "regulatory_regime": None,  # PolicyAgent (Phase 5) computes this
        "is_18c_pre_commercial": (listing_type == "18C-PRE") if listing_type else None,
        "ah_pair_a_code": row["a_share_code"] if to_bool(row["is_a_h"]) else None,
    }


def map_ipo_pricing(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": ns_uuid(NS_IPO, f"pricing:{row['ipo_id']}"),
        "ipo_id": ns_uuid(NS_IPO, row["ipo_id"]),
        "price_range_low": to_decimal(row["offer_price_low"]),
        "price_range_high": to_decimal(row["offer_price_high"]),
        "final_price": to_decimal(row["offer_price_hkd"]),
        "intl_oversubscription": to_decimal(row["intl_oversub"]),
        "retail_oversubscription": to_decimal(row["public_oversub"]),
        "margin_subscription_multiple": None,  # NACS doesn't track margin
        "allocation_mechanism": None,
        "final_public_allocation_pct": None,
    }


def map_ipo_postmarket(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": ns_uuid(NS_IPO, f"postmarket:{row['ipo_id']}"),
        "ipo_id": ns_uuid(NS_IPO, row["ipo_id"]),
        # Spec §5 scalar columns (NACS-compatible)
        "day1_return": to_decimal(row["return_d1_close"]),
        "day5_return": None,  # NACS has return_d30 but no d5; leave null
        "day22_return": to_decimal(row["return_d30"]),  # ~22 trading days = 30 calendar
        "day126_return": to_decimal(row["return_m6"]),  # ~126 trading days = 6 months
        "day127_return": to_decimal(row["return_unlock_d30"]),  # post-lockup proxy
        "day252_return": to_decimal(row["return_m12"]),
        "max_drawdown_d126": to_decimal(row["max_drawdown_m6"]),
        "cornerstone_held_after_lockup": None,  # not in NACS; populated later
        # ADR 0007: JSONB stays null for NACS legacy; iFind backfill in Phase 8
        "returns_by_day": None,
        "cornerstone_held_pct_by_day": None,
    }


def map_cornerstone_investor(
    row: sqlite3.Row,
    *,
    aliases: list[dict[str, Any]],
    ultimate_holder: str | None = None,
) -> dict[str, Any]:
    return {
        "id": ns_uuid(NS_CORNERSTONE, row["cornerstone_id"]),
        "name_zh": row["name_zh"],
        "name_en": row["canonical_name"],
        "category": row["cornerstone_type"],
        "parent_org": row["parent_entity"],
        # NACS stores ultimate_holder per-link (one investor can appear in many
        # IPOs with the same ultimate_holder). We pre-aggregate from
        # ipo_cornerstone_link in migrate_cornerstone_investors.
        "ultimate_holder": ultimate_holder or row["parent_entity"],
        "home_country": row["country_of_origin"],
        "signal_strength_score": None,  # recomputed in Phase 7.5
        "aliases": {"items": aliases} if aliases else None,
        "extra_metadata": {
            "nacs_cornerstone_id": row["cornerstone_id"],
            "aum_usd_latest": float(row["aum_usd_latest"]) if row["aum_usd_latest"] is not None else None,
            "aum_asof_date": row["aum_asof_date"],
            "is_chinese": bool(row["is_chinese"]),
            "is_longterm": bool(row["is_longterm"]),
            "notes": row["notes"],
        },
    }


def map_cornerstone_investment(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": ns_uuid(NS_INVESTMENT, str(row["link_id"])),
        "ipo_id": ns_uuid(NS_IPO, row["ipo_id"]),
        "investor_id": ns_uuid(NS_CORNERSTONE, row["cornerstone_id"]),
        "commitment_amount_hkd": to_decimal(row["ticket_size_hkd"]),
        "pct_of_offering": to_decimal(row["subscribe_pct"]),
        "lockup_months": to_int(row["lockup_months_actual"]),
        # NACS v8 SQLite schema doesn't track disclosure_date — leave null;
        # Phase 9 re-loader from primary HKEX filings will backfill.
        "disclosure_date": None,
        "is_anchor": False,  # NACS doesn't distinguish anchor vs cornerstone
    }


def map_company(row: sqlite3.Row) -> dict[str, Any]:
    """Build a Company stub from an ipo_master row.

    A Company is the issuer entity (lives across pre/post IPO); we key it by
    stock_code (or NACS ipo_id when stock_code is blank, e.g. for un-listed
    rows). financial_snapshots later attach via company_id.
    """
    key = row["stock_code"] or row["ipo_id"]
    return {
        "id": ns_uuid(NS_COMPANY, key),
        "name_zh": row["company_name_zh"],
        "name_en": row["company_name_en"],
        "hk_stock_code": row["stock_code"],
        "a_share_code": row["a_share_code"] if to_bool(row["is_a_h"]) else None,
        "industry_code": row["gics_l2"],
    }


def map_financial_snapshot(
    row: sqlite3.Row,
    *,
    company_id: UUID,
) -> dict[str, Any]:
    """ipo_financials row -> financial_snapshots row."""
    return {
        "id": ns_uuid(
            NS_COMPANY,
            f"fin:{row['stock_code']}:{row['report_year']}",
        ),
        "company_id": company_id,
        "fiscal_year": int(row["report_year"]),
        "fiscal_period": "FY",
        "period_end": None,
        "source": "nacs_legacy",
        "revenue_rmb": to_decimal(row["revenue_cny"]),
        "gross_profit_rmb": None,  # NACS only stores margin %, not absolute
        "gross_margin": _pct_to_ratio(row["gross_margin"]),
        "rd_expense_rmb": None,
        "rd_pct_of_revenue": None,
        "net_profit_rmb": None,
        "adjusted_net_profit_rmb": None,
        "operating_cash_flow_rmb": None,
        "cash_balance_rmb": None,
    }


# ---------------------------------------------------------------------------
# Migration driver
# ---------------------------------------------------------------------------


async def migrate_ipo_events(con: sqlite3.Connection, repo: IPOEventRepository) -> int:
    cur = con.execute("SELECT * FROM ipo_master")
    rows = [map_ipo_event(r) for r in cur.fetchall()]
    n: int = await repo.bulk_upsert(rows, conflict_cols=["id"])
    log.info("migrated_ipo_events", count=n)
    return n


async def migrate_ipo_pricings(
    con: sqlite3.Connection,
    repo: IPOPricingRepository,
) -> int:
    cur = con.execute("SELECT * FROM ipo_master")
    rows = [map_ipo_pricing(r) for r in cur.fetchall()]
    n: int = await repo.bulk_upsert(rows, conflict_cols=["ipo_id"])
    log.info("migrated_ipo_pricings", count=n)
    return n


async def migrate_ipo_postmarket(
    con: sqlite3.Connection,
    repo: IPOPostMarketRepository,
) -> int:
    cur = con.execute("SELECT * FROM ipo_returns")
    rows = [map_ipo_postmarket(r) for r in cur.fetchall()]
    n: int = await repo.bulk_upsert(rows, conflict_cols=["ipo_id"])
    log.info("migrated_ipo_postmarket", count=n)
    return n


async def migrate_cornerstone_investors(
    con: sqlite3.Connection,
    repo: CornerstoneInvestorRepository,
) -> int:
    # Aggregate ultimate_holder per cornerstone_id from the link table
    # (NACS stores ultimate_holder per-link; cornerstone_master.parent_entity
    # is empty across the corpus). For ADR 0005 §2 Cluster Bonus to work,
    # every investor MUST have ultimate_holder populated.
    holder_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cur = con.execute(
        "SELECT cornerstone_id, ultimate_holder FROM ipo_cornerstone_link "
        "WHERE ultimate_holder IS NOT NULL AND ultimate_holder != ''"
    )
    for link in cur.fetchall():
        holder_counts[link["cornerstone_id"]][link["ultimate_holder"]] += 1
    # Pick the most-frequently-observed ultimate_holder per investor
    holder_by_id: dict[str, str] = {
        cs_id: max(holders.items(), key=lambda kv: kv[1])[0]
        for cs_id, holders in holder_counts.items()
    }
    log.info(
        "ultimate_holder_aggregated_from_links",
        investors_with_holder=len(holder_by_id),
        total_links_scanned=sum(sum(h.values()) for h in holder_counts.values()),
    )

    # Group aliases by cornerstone_id
    aliases_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cur = con.execute("SELECT * FROM cornerstone_aliases")
    for a in cur.fetchall():
        aliases_by_id[a["cornerstone_id"]].append(
            {
                "text": a["alias_text"],
                "type": a["alias_type"],
                "confidence": a["match_confidence"],
            }
        )

    cur = con.execute("SELECT * FROM cornerstone_master")
    rows = [
        map_cornerstone_investor(
            r,
            aliases=aliases_by_id.get(r["cornerstone_id"], []),
            ultimate_holder=holder_by_id.get(r["cornerstone_id"]),
        )
        for r in cur.fetchall()
    ]
    n: int = await repo.bulk_upsert(rows, conflict_cols=["id"])
    log.info(
        "migrated_cornerstone_investors",
        count=n,
        aliases_merged=sum(len(v) for v in aliases_by_id.values()),
        ultimate_holder_populated=sum(1 for r in rows if r["ultimate_holder"]),
    )
    return n


async def migrate_cornerstone_investments(
    con: sqlite3.Connection,
    inv_repo: CornerstoneInvestmentRepository,
    investor_repo: CornerstoneInvestorRepository,
) -> int:
    # First pass: collect any cornerstone_id values that don't exist in master
    # so we can create stub investors (NACS data has orphans).
    cur = con.execute(
        "SELECT DISTINCT cornerstone_id, cornerstone_name FROM ipo_cornerstone_link "
        "WHERE cornerstone_id NOT IN (SELECT cornerstone_id FROM cornerstone_master)"
    )
    orphan_rows = cur.fetchall()
    if orphan_rows:
        stubs = [
            {
                "id": ns_uuid(NS_CORNERSTONE, r["cornerstone_id"]),
                "name_zh": None,
                "name_en": r["cornerstone_name"],
                "category": "other",
                "extra_metadata": {
                    "nacs_cornerstone_id": r["cornerstone_id"],
                    "source": "orphan_from_link_table",
                },
            }
            for r in orphan_rows
        ]
        await investor_repo.bulk_upsert(stubs, conflict_cols=["id"])
        log.warning("created_orphan_investor_stubs", count=len(stubs))

    # Now ingest link rows
    cur = con.execute("SELECT * FROM ipo_cornerstone_link")
    rows = [map_cornerstone_investment(r) for r in cur.fetchall()]

    # Drop links pointing to IPOs we didn't migrate (shouldn't happen but defensive)
    valid_ipo_ids = {r["id"] for r in rows}
    skipped = 0
    if valid_ipo_ids:
        existing_stmt = select(IPOEvent.id).where(
            IPOEvent.id.in_({r["ipo_id"] for r in rows})
        )
        existing = {row[0] for row in (await inv_repo.session.execute(existing_stmt)).all()}
        before = len(rows)
        rows = [r for r in rows if r["ipo_id"] in existing]
        skipped = before - len(rows)
        if skipped:
            log.warning("skipped_links_orphan_ipo", count=skipped)

    n: int = await inv_repo.bulk_upsert(rows, conflict_cols=["id"])
    log.info("migrated_cornerstone_investments", count=n, orphan_skipped=skipped)
    return n


async def migrate_companies_and_financials(con: sqlite3.Connection) -> tuple[int, int]:
    """Companies derive from ipo_master; financial snapshots from ipo_financials."""
    factory = async_session_factory()
    async with factory() as session:
        # 1. Companies
        cur = con.execute("SELECT * FROM ipo_master")
        company_rows = [map_company(r) for r in cur.fetchall()]
        # Build stock_code -> company_id index (for financial_snapshots join)
        code_to_company_id: dict[str, UUID] = {}
        for ipo_row, company_row in zip(
            con.execute("SELECT * FROM ipo_master").fetchall(),
            company_rows,
            strict=True,
        ):
            if ipo_row["stock_code"]:
                code_to_company_id[ipo_row["stock_code"]] = company_row["id"]

        stmt = pg_insert(Company.__table__).values(company_rows)
        update_cols = {k: stmt.excluded[k] for k in company_rows[0] if k != "id"}
        await session.execute(
            stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
        )
        log.info("migrated_companies", count=len(company_rows))

        # 2. Financial snapshots
        cur = con.execute("SELECT * FROM ipo_financials")
        fin_rows: list[dict[str, Any]] = []
        skipped_fin = 0
        for r in cur.fetchall():
            company_id = code_to_company_id.get(r["stock_code"])
            if company_id is None:
                skipped_fin += 1
                continue
            fin_rows.append(map_financial_snapshot(r, company_id=company_id))

        if fin_rows:
            stmt2 = pg_insert(FinancialSnapshotRow.__table__).values(fin_rows)
            update_cols2 = {k: stmt2.excluded[k] for k in fin_rows[0] if k != "id"}
            await session.execute(
                stmt2.on_conflict_do_update(index_elements=["id"], set_=update_cols2)
            )
        log.info(
            "migrated_financial_snapshots",
            count=len(fin_rows),
            skipped_no_company=skipped_fin,
        )

        await session.commit()
        return len(company_rows), len(fin_rows)


def dump_market_env_cache(con: sqlite3.Connection, out_dir: Path) -> Path:
    """ADR 0007 §1: dump as JSON fixture; NOT a PG table."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = con.execute(
        "SELECT * FROM market_environment_cache ORDER BY asof_month ASC"
    )
    records = [dict(r) for r in cur.fetchall()]
    out = out_dir / "market_env_cache.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "source": "nacs_v8_sqlite_migration",
                "dumped_at": datetime.now(UTC).isoformat(),
                "row_count": len(records),
                "rows": records,
            },
            fh,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    log.info("dumped_market_env_cache", path=str(out), count=len(records))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_migration(
    *,
    sqlite_path: Path,
    fixture_dir: Path,
    do_backup: bool,
    dry_run: bool,
) -> dict[str, int]:
    if do_backup:
        backup_sqlite(sqlite_path)

    con = open_sqlite(sqlite_path)
    counts: dict[str, int] = {}
    factory = async_session_factory()
    async with factory() as session:
        try:
            # Order matters: parents before children.
            ipo_repo = IPOEventRepository(session)
            counts["ipo_events"] = await migrate_ipo_events(con, ipo_repo)

            pricing_repo = IPOPricingRepository(session)
            counts["ipo_pricings"] = await migrate_ipo_pricings(con, pricing_repo)

            postmarket_repo = IPOPostMarketRepository(session)
            counts["ipo_postmarket"] = await migrate_ipo_postmarket(con, postmarket_repo)

            inv_repo = CornerstoneInvestorRepository(session)
            counts["cornerstone_investors"] = await migrate_cornerstone_investors(
                con, inv_repo
            )

            link_repo = CornerstoneInvestmentRepository(session)
            counts["cornerstone_investments"] = await migrate_cornerstone_investments(
                con, link_repo, inv_repo
            )

            if dry_run:
                log.warning("dry_run_rollback")
                await session.rollback()
            else:
                await session.commit()
        except Exception:
            await session.rollback()
            log.exception("migration_failed_rolled_back")
            raise

    # Companies + financials need their own session (separate commit boundary).
    if not dry_run:
        comp, fin = await migrate_companies_and_financials(con)
        counts["companies"] = comp
        counts["financial_snapshots"] = fin

    dump_market_env_cache(con, fixture_dir)
    counts["market_env_cache_json"] = 1

    con.close()
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE_PATH)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(level="INFO", json=False)

    log.info(
        "migration_starting",
        sqlite=str(args.sqlite),
        fixture_dir=str(args.fixture_dir),
        backup=not args.no_backup,
        dry_run=args.dry_run,
        target_db=get_settings().database.host,
    )

    try:
        counts = asyncio.run(
            run_migration(
                sqlite_path=args.sqlite,
                fixture_dir=args.fixture_dir,
                do_backup=not args.no_backup,
                dry_run=args.dry_run,
            )
        )
    except Exception:
        log.exception("migration_failed")
        return 1

    log.info("migration_complete", **counts)
    print("\n=== Migration complete ===")
    for table, n in counts.items():
        print(f"  {table:32s} {n:>8d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
