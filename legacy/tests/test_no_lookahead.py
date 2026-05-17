"""
P0 look-ahead 防御性测试.

覆盖:
    1. get_financials(asof) 严格只返回 report_year < asof.year 的财务
    2. pricing_date 在 ipo_master 中必须 < listing_date (P0-#2 不变量)

任何回归打破这些不变量, IC/t-stat 报告会被未来知识污染.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))


@pytest.fixture
def fin_db(tmp_path):
    """构造一只 2022 年上市 IPO + 4 年财务数据 (2022/2023/2024/2025)."""
    db_path = tmp_path / "fin.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE ipo_financials (
            stock_code TEXT NOT NULL,
            report_year INTEGER NOT NULL,
            revenue_cny REAL,
            gross_margin REAL,
            net_margin REAL,
            roe REAL,
            PRIMARY KEY (stock_code, report_year)
        )
    """)
    rows = [
        ("0001.HK", 2022, 100.0, 30.0, 10.0, 8.0),
        ("0001.HK", 2023, 150.0, 35.0, 15.0, 12.0),  # ← 2022 上市时不可知
        ("0001.HK", 2024, 200.0, 40.0, 18.0, 15.0),  # ← 同上
        ("0001.HK", 2025, 250.0, 45.0, 20.0, 18.0),  # ← 同上
    ]
    conn.executemany(
        "INSERT INTO ipo_financials VALUES (?,?,?,?,?,?)", rows
    )
    conn.commit()
    yield conn
    conn.close()


def test_get_financials_filters_future_years(fin_db):
    """2022 年定价的 IPO 不应读到 2023+ 财务.

    cutoff = year=2022, 因测试数据集中没有 < 2022 的记录, 应返回 None.
    重点验证: 没有任何 >= 2022 的年份被返回.
    """
    from run_v7_backtest import get_financials
    fin = get_financials(fin_db, "0001.HK", asof=date(2022, 1, 11))
    if fin is None:
        # 完全过滤掉 — 期望行为
        return
    years = sorted(fin.keys())
    assert all(y < 2022 for y in years), f"看到了未来年份: {years}"


def test_get_financials_2023_pricing_sees_2022(fin_db):
    """2023 年定价时, 2022 年报已可知, 应能读到."""
    from run_v7_backtest import get_financials
    fin = get_financials(fin_db, "0001.HK", asof=date(2023, 6, 15))
    assert fin is not None
    assert set(fin.keys()) == {2022}, f"应只见 2022, 实际 {set(fin.keys())}"
    assert fin[2022]["revenue"] == 100.0


def test_get_financials_2026_pricing_sees_all_past(fin_db):
    """2026 年定价时, 2022/2023/2024/2025 全部可知."""
    from run_v7_backtest import get_financials
    fin = get_financials(fin_db, "0001.HK", asof=date(2026, 3, 1))
    assert fin is not None
    assert set(fin.keys()) == {2022, 2023, 2024, 2025}


def test_real_db_pricing_date_strict_before_listing():
    """生产 DB: pricing_date 必须 < listing_date (P0-#2 不变量)."""
    db = _ROOT / "data" / "nacs_real.db"
    if not db.exists():
        pytest.skip("生产 DB 不存在, 跳过")
    conn = sqlite3.connect(db)
    n_bad = conn.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE pricing_date >= listing_date"
    ).fetchone()[0]
    conn.close()
    assert n_bad == 0, f"{n_bad} 行 pricing_date >= listing_date (look-ahead 风险)"


def test_real_db_no_future_financials():
    """生产 DB: 财务表中 report_year > listing_year 的行存在 (这是数据本身),
    但任何 build_offering 调用必须只读到 < pricing_date.year 的部分.
    这里只验证: 用 cutoff 后查询数为 0 行越界."""
    db = _ROOT / "data" / "nacs_real.db"
    if not db.exists():
        pytest.skip("生产 DB 不存在, 跳过")
    conn = sqlite3.connect(db)
    # 抽 5 只 2022 上市的 IPO, 验证带 cutoff 后只剩 < 2022 的年份
    rows = conn.execute("""
        SELECT m.stock_code, m.pricing_date FROM ipo_master m
        WHERE m.listing_date < '2023-01-01' LIMIT 5
    """).fetchall()
    if not rows:
        pytest.skip("无 2022 IPO 样本")
    sys.path.insert(0, str(_ROOT))
    from run_v7_backtest import get_financials
    for sc, pd_str in rows:
        asof = date.fromisoformat(pd_str[:10])
        fin = get_financials(conn, sc, asof=asof)
        if fin is None:
            continue
        max_yr = max(fin.keys())
        assert max_yr < asof.year, (
            f"{sc} pricing={asof} 看到 report_year={max_yr} (look-ahead!)"
        )
    conn.close()
