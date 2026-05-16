"""Unit tests for the SQLite -> PG ETL mappers in scripts/migrate_sqlite_to_pg.py.

Verifies (ADR 0005 §1 + ADR 0007 + ADR 0006 §Progress):
- Stable UUID5 generation across re-runs (idempotency anchor)
- Type coercion robustness (None / "" / messy strings / Decimal precision)
- Listing-chapter normalization to spec ListingType enum
- Pct -> ratio conversion for NACS percent-margin fields
- IPOPostMarket mapping leaves JSONB returns_by_day null (ADR 0007 §write rules)
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from uuid import UUID

import pytest
from scripts.migrate_sqlite_to_pg import (
    NS_CORNERSTONE,
    NS_INVESTMENT,
    NS_IPO,
    _pct_to_ratio,
    map_cornerstone_investment,
    map_cornerstone_investor,
    map_ipo_event,
    map_ipo_postmarket,
    map_ipo_pricing,
    normalize_listing_type,
    ns_uuid,
    to_bool,
    to_date,
    to_decimal,
    to_int,
)

# ---------------------------------------------------------------------------
# UUID5 stability — re-runs MUST produce identical ids
# ---------------------------------------------------------------------------


def test_ns_uuid_stable_for_same_input() -> None:
    a = ns_uuid(NS_IPO, "HK_03296_2026")
    b = ns_uuid(NS_IPO, "HK_03296_2026")
    assert a == b
    assert isinstance(a, UUID)


def test_ns_uuid_distinct_across_namespaces() -> None:
    """Same key in different namespaces must produce different UUIDs."""
    key = "shared-key"
    assert ns_uuid(NS_IPO, key) != ns_uuid(NS_CORNERSTONE, key)
    assert ns_uuid(NS_CORNERSTONE, key) != ns_uuid(NS_INVESTMENT, key)


def test_ns_uuid_distinct_for_different_keys() -> None:
    assert ns_uuid(NS_IPO, "A") != ns_uuid(NS_IPO, "B")


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("3.14", Decimal("3.14")),
        (3.14, Decimal("3.14")),
        (1, Decimal("1")),
        ("not a number", None),
    ],
)
def test_to_decimal(raw: object, expected: Decimal | None) -> None:
    assert to_decimal(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("True", True),
        ("no", False),
        ("0", False),
    ],
)
def test_to_bool(raw: object, expected: bool | None) -> None:
    assert to_bool(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected_year"),
    [
        (None, None),
        ("", None),
        ("2024-06-13", 2024),
        ("2024/06/13", 2024),
        ("20240613", 2024),
    ],
)
def test_to_date(raw: object, expected_year: int | None) -> None:
    result = to_date(raw)
    if expected_year is None:
        assert result is None
    else:
        assert result is not None
        assert result.year == expected_year


def test_to_int_handles_garbage() -> None:
    assert to_int(None) is None
    assert to_int("") is None
    assert to_int("42") == 42
    assert to_int(3.7) == 3
    assert to_int("not-an-int") is None


def test_pct_to_ratio() -> None:
    assert _pct_to_ratio("35.5") == Decimal("0.355")
    assert _pct_to_ratio(100) == Decimal("1.00")
    assert _pct_to_ratio(0) == Decimal("0")
    assert _pct_to_ratio(None) is None


# ---------------------------------------------------------------------------
# Listing chapter normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18C_commercial", "18C-COMM"),
        ("18c_commercial", "18C-COMM"),
        ("18C_pre_commercial", "18C-PRE"),
        ("18A", "18A"),
        ("AH", "AH"),
        ("MB_TECH", "MB-TECH"),
        ("MB", "MB-OTHER"),
        ("something-weird", "MB-OTHER"),  # fallback
        ("", None),
        (None, None),
    ],
)
def test_normalize_listing_type(raw: str | None, expected: str | None) -> None:
    assert normalize_listing_type(raw) == expected


# ---------------------------------------------------------------------------
# Row-level mappers — synthesize SQLite Row objects to exercise mapping code
# ---------------------------------------------------------------------------


def _row(columns: dict) -> sqlite3.Row:
    """Build a sqlite3.Row from a plain dict for test setup."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    cols = ", ".join(columns.keys())
    placeholders = ", ".join("?" * len(columns))
    cur = con.execute(f"CREATE TABLE t ({cols})")
    cur.execute(f"INSERT INTO t VALUES ({placeholders})", list(columns.values()))
    cur = con.execute("SELECT * FROM t")
    return cur.fetchone()


def _ipo_master_row(**overrides: object) -> sqlite3.Row:
    base = {
        "ipo_id": "HK_02228_2024",
        "stock_code": "2228.HK",
        "company_name_zh": "晶泰控股",
        "company_name_en": "QuantumPharm Inc.",
        "listing_date": "2024-06-13",
        "pricing_date": "2024-06-06",
        "listing_chapter": "18C_commercial",
        "is_a_h": 0,
        "a_share_code": None,
        "gics_l2": "BIOTECH-AI",
        "offer_price_hkd": 5.28,
        "offer_price_low": 5.03,
        "offer_price_high": 6.03,
        "offering_size_hkd": 1000000000,
        "intl_oversub": 12.5,
        "public_oversub": 102.3,
    }
    base.update(overrides)
    return _row(base)


def test_map_ipo_event_basics() -> None:
    row = _ipo_master_row()
    mapped = map_ipo_event(row)
    assert mapped["stock_code"] == "2228.HK"
    assert mapped["listing_type"] == "18C-COMM"
    assert mapped["industry_code"] == "BIOTECH-AI"
    assert mapped["issue_size_hkd"] == Decimal("1000000000")
    assert mapped["is_18c_pre_commercial"] is False  # CH18C-COMM not pre
    assert mapped["ah_pair_a_code"] is None  # is_a_h=0
    assert isinstance(mapped["id"], UUID)


def test_map_ipo_event_ah() -> None:
    row = _ipo_master_row(is_a_h=1, a_share_code="300750.SZ", listing_chapter="AH")
    mapped = map_ipo_event(row)
    assert mapped["listing_type"] == "AH"
    assert mapped["ah_pair_a_code"] == "300750.SZ"


def test_map_ipo_pricing_uses_correct_columns() -> None:
    row = _ipo_master_row()
    mapped = map_ipo_pricing(row)
    assert mapped["price_range_low"] == Decimal("5.03")
    assert mapped["price_range_high"] == Decimal("6.03")
    assert mapped["final_price"] == Decimal("5.28")
    assert mapped["intl_oversubscription"] == Decimal("12.5")
    assert mapped["retail_oversubscription"] == Decimal("102.3")
    # Foreign key matches the ipo_event id
    expected_ipo_uuid = ns_uuid(NS_IPO, "HK_02228_2024")
    assert mapped["ipo_id"] == expected_ipo_uuid


def _ipo_returns_row(**overrides: object) -> sqlite3.Row:
    base = {
        "ipo_id": "HK_02228_2024",
        "return_d1_close": 0.15,
        "return_d30": 0.22,
        "return_m3": 0.18,
        "return_m6": 0.30,
        "return_m12": 0.45,
        "return_unlock_d30": 0.05,
        "return_unlock_d90": -0.10,
        "max_drawdown_m6": -0.12,
    }
    base.update(overrides)
    return _row(base)


def test_map_ipo_postmarket_adr_0007_compliance() -> None:
    """ADR 0007 §write rules: NACS data fills 6 scalar cols, JSONB stays NULL."""
    row = _ipo_returns_row()
    mapped = map_ipo_postmarket(row)
    # spec §5 scalar columns populated
    assert mapped["day1_return"] == Decimal("0.15")
    assert mapped["day22_return"] == Decimal("0.22")  # NACS return_d30 ~ d22
    assert mapped["day126_return"] == Decimal("0.30")  # NACS return_m6 ~ d126
    assert mapped["day252_return"] == Decimal("0.45")
    # ADR 0007: JSONB stays null on NACS migration
    assert mapped["returns_by_day"] is None
    assert mapped["cornerstone_held_pct_by_day"] is None


def _cornerstone_master_row(**overrides: object) -> sqlite3.Row:
    base = {
        "cornerstone_id": "CS001",
        "canonical_name": "China Investment Corp",
        "name_zh": "中投公司",
        "cornerstone_type": "sovereign",
        "parent_entity": "国资委",
        "country_of_origin": "CN",
        "aum_usd_latest": 1_300_000_000_000.0,
        "aum_asof_date": "2024-12-31",
        "is_chinese": 1,
        "is_longterm": 1,
        "notes": "Sovereign wealth fund",
    }
    base.update(overrides)
    return _row(base)


def test_map_cornerstone_investor_merges_aliases() -> None:
    row = _cornerstone_master_row()
    aliases = [
        {"text": "CIC", "type": "abbreviation", "confidence": 1.0},
        {"text": "China Investment", "type": "english", "confidence": 0.9},
    ]
    mapped = map_cornerstone_investor(row, aliases=aliases)
    assert mapped["name_zh"] == "中投公司"
    assert mapped["category"] == "sovereign"
    assert mapped["aliases"] == {"items": aliases}
    assert mapped["extra_metadata"]["aum_usd_latest"] == 1.3e12
    assert mapped["extra_metadata"]["is_chinese"] is True
    assert mapped["extra_metadata"]["nacs_cornerstone_id"] == "CS001"


def test_map_cornerstone_investor_no_aliases_sets_null() -> None:
    row = _cornerstone_master_row()
    mapped = map_cornerstone_investor(row, aliases=[])
    assert mapped["aliases"] is None


def _link_row(**overrides: object) -> sqlite3.Row:
    base = {
        "link_id": 1,
        "ipo_id": "HK_02228_2024",
        "cornerstone_id": "CS001",
        "ticket_size_hkd": 100_000_000,
        "subscribe_pct": 0.05,
        "lockup_months_actual": 6,
        "as_of_date": "2024-06-06",
    }
    base.update(overrides)
    return _row(base)


def test_map_cornerstone_investment_links_correctly() -> None:
    row = _link_row()
    mapped = map_cornerstone_investment(row)
    assert mapped["ipo_id"] == ns_uuid(NS_IPO, "HK_02228_2024")
    assert mapped["investor_id"] == ns_uuid(NS_CORNERSTONE, "CS001")
    assert mapped["commitment_amount_hkd"] == Decimal("100000000")
    assert mapped["pct_of_offering"] == Decimal("0.05")
    assert mapped["lockup_months"] == 6
    assert mapped["is_anchor"] is False
