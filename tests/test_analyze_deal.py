"""
analyze_deal.py 功能测试.

仅测试可独立验证的部分 (scenario 选取, asof 解析, deal 评估闭环);
CLI 主流程通过 subprocess 跑一次端到端验证.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


# =============================================================================
# _scenario_prices
# =============================================================================

def test_scenario_listed_uses_final_price(empty_db):
    """status='listed' + 已有 offer_price_hkd → 只跑 final 一档"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from analyze_deal import _scenario_prices

    with sqlite3.connect(str(empty_db)) as c:
        c.row_factory = sqlite3.Row
        c.execute("""
            INSERT INTO ipo_master (ipo_id, stock_code, listing_date, listing_chapter,
                                     status, offer_price_hkd, offer_price_low, offer_price_high)
            VALUES ('HK_001', '0001.HK', '2024-01-01', 'main_board_profitable',
                    'listed', 8.5, 7.5, 9.0)
        """)
        row = c.execute("SELECT * FROM ipo_master WHERE ipo_id='HK_001'").fetchone()
    scenarios = _scenario_prices(row)
    assert scenarios == [("final", 8.5)]


def test_scenario_prospectus_with_range(empty_db):
    """status='prospectus' + 有区间 → low/mid/high 三档"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from analyze_deal import _scenario_prices

    with sqlite3.connect(str(empty_db)) as c:
        c.row_factory = sqlite3.Row
        future = (date.today() + timedelta(days=120)).isoformat()
        c.execute("""
            INSERT INTO ipo_master (ipo_id, stock_code, listing_date, listing_chapter,
                                     status, offer_price_low, offer_price_high)
            VALUES ('HK_002', '0002.HK', ?, 'main_board_profitable',
                    'prospectus', 8.0, 10.0)
        """, (future,))
        row = c.execute("SELECT * FROM ipo_master WHERE ipo_id='HK_002'").fetchone()
    scenarios = _scenario_prices(row)
    names = [s[0] for s in scenarios]
    prices = [s[1] for s in scenarios]
    assert names == ["low", "mid", "high"]
    assert prices == [8.0, 9.0, 10.0]


def test_scenario_no_data_fallback_to_mid(empty_db):
    """没有任何价格数据 → mid=1.0 兜底"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from analyze_deal import _scenario_prices

    with sqlite3.connect(str(empty_db)) as c:
        c.row_factory = sqlite3.Row
        future = (date.today() + timedelta(days=60)).isoformat()
        c.execute("""
            INSERT INTO ipo_master (ipo_id, stock_code, listing_date, listing_chapter, status)
            VALUES ('HK_003', '0003.HK', ?, 'main_board_profitable', 'prospectus')
        """, (future,))
        row = c.execute("SELECT * FROM ipo_master WHERE ipo_id='HK_003'").fetchone()
    scenarios = _scenario_prices(row)
    assert scenarios == [("mid", 1.0)]


# =============================================================================
# CLI smoke (跑一次, 验证不崩 + 退出码 0)
# =============================================================================

@pytest.mark.slow
def test_cli_single_deal_smoke(empty_db, project_root):
    """单 deal CLI smoke: 用 empty_db 灌一只 IPO, 跑 analyze_deal."""
    # 先灌一只 listed IPO + ipo_returns 进去
    with sqlite3.connect(str(empty_db)) as c:
        c.execute("""
            INSERT INTO ipo_master (ipo_id, stock_code, company_name_zh, listing_date,
                                     listing_chapter, status, offer_price_hkd,
                                     offering_size_hkd, intl_oversub, public_oversub,
                                     pricing_in_range, sponsor_primary, sponsor_tier,
                                     gics_l2, lockup_months, post_ipo_shares,
                                     pe_at_offer, pe_peer_median,
                                     overhang_ratio, fundamental_risk_score,
                                     pe_vs_history_pct, peer_lockup_avg_drawdown,
                                     pricing_date)
            VALUES ('HK_TEST', '9999.HK', 'Test Co', '2024-06-01',
                    'main_board_profitable', 'listed', 10.0,
                    1e9, 5.0, 10.0, 0.6, 'UBS', 1,
                    'IT', 6, 100000000, 15.0, 18.0,
                    1.0, 0.3, 0.5, 0.1,
                    '2024-05-25')
        """)
        c.commit()

    env = {**os.environ, "PYTHONPATH": f"{project_root}:{project_root}/src"}
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "analyze_deal.py"),
         "--stock-code", "9999.HK", "--db", str(empty_db)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "9999.HK" in result.stdout
    assert "NACS_adjusted" in result.stdout or "NACS_adj" in result.stdout
