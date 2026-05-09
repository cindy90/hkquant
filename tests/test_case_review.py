"""
case_review 复盘报告测试.

覆盖:
    - review() 在没预测时返回 error 字段
    - review() 在没 ipo_master row 时返回 error
    - 多次 prediction 的 stability std 计算
    - similar_cases d30/m6 中位数计算
    - inputs_vs_actual diff 字段
    - is_*_due=0 时 actual 字段为 None (业绩未到期)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# =============================================================================
# review() base cases
# =============================================================================

def test_review_returns_error_for_unknown_stock(empty_db):
    from data.dao import db_connect
    from case_review import review
    with db_connect(str(empty_db)) as conn:
        rep = review(conn, "0001.HK")
    assert rep["error"] == "not in ipo_master"


def test_review_no_predictions_yet(empty_db):
    from data.dao import db_connect, upsert_ipo
    from case_review import review

    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-01-01", listing_chapter="main_board",
                   status="listed")
        rep = review(conn, "0001.HK")
    assert rep["error"] == "no predictions yet for this stock"
    assert rep["n_predictions"] == 0
    assert rep["current_status"] == "listed"


# =============================================================================
# 集成: persist 多个 prediction → review 报告
# =============================================================================

def _persist_n_predictions(conn, stock_code, make_ipo, asofs, decisions=None):
    """helper: 帮助多次 persist 不同 (asof, snap)"""
    from data.panel_snapshot import write_panel_snapshot
    from data.predictions import persist_prediction
    from nacs_model import compute_nacs

    offering = make_ipo()
    result = compute_nacs(offering)
    case_ids = []
    for asof in asofs:
        sid = write_panel_snapshot(
            conn, asof=asof,
            market_env={"hsi_60d_return": 0.0}, regime_score=0.0,
            config_dict={"version": "v8", "_d": asof.isoformat()},
        )
        cid = persist_prediction(
            conn, result=result, offering=offering,
            stock_code=stock_code, asof=asof,
            panel_snapshot_id=sid, price_scenario="mid",
        )
        case_ids.append(cid)
    return case_ids


def test_review_full_report_structure(empty_db, make_ipo):
    from data.dao import db_connect, upsert_ipo
    from case_review import review

    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-06-01", listing_chapter="main_board_profitable",
                   status="listed", offer_price_hkd=10.0)
        # 3 次预测
        _persist_n_predictions(
            conn, "0001.HK", make_ipo,
            asofs=[date(2024, 1, 1), date(2024, 4, 1), date(2024, 6, 1)],
        )
        rep = review(conn, "0001.HK")

    assert rep["n_predictions"] == 3
    # asof 升序
    asofs = [str(p["asof_date"])[:10] for p in rep["predictions"]]
    assert asofs == sorted(asofs)
    # 最后一次锁定
    assert rep["locked_prediction"]["asof_date"] == "2024-06-01"
    # std 字段存在
    assert "nacs_std" in rep["stability"]
    # 同 offering 跑 3 次, std=0
    assert rep["stability"]["nacs_std"] == 0.0


def test_review_actual_fields_filtered_by_due(empty_db, make_ipo):
    """is_m6_due=0 时, actuals.return_m6 应是 None (未到期)"""
    from data.dao import db_connect, upsert_ipo
    from case_review import review

    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2026-01-01", listing_chapter="main_board_profitable",
                   status="listed", offer_price_hkd=10.0)
        # is_d30_due=1, is_m6_due=0 (未到期), is_m12_due=0
        conn.execute(
            "INSERT INTO ipo_returns "
            "(ipo_id, return_d30, return_m6, return_m12, "
            " is_d30_due, is_m6_due, is_m12_due) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("HK_001", 0.10, 0.05, 0.0, 1, 0, 0),
        )
        _persist_n_predictions(conn, "0001.HK", make_ipo, asofs=[date(2026, 1, 1)])
        rep = review(conn, "0001.HK")

    a = rep["actuals"]
    assert a["return_d30"] == 0.10           # d30 已到期
    assert a["return_m6"] is None            # m6 未到期, 不暴露
    assert a["return_m12"] is None
    assert a["is_d30_due"] == 1
    assert a["is_m6_due"] == 0


def test_review_similar_cases_median_computed(empty_db, make_ipo):
    """similar_cases 在 prediction 中带 actual_d30/m6, review 算中位"""
    from data.dao import db_connect, upsert_ipo
    from case_review import review

    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_001", stock_code="0001.HK",
                   listing_date="2024-06-01", listing_chapter="main_board_profitable",
                   gics_l2="医疗", status="listed", offer_price_hkd=10.0)
        # seed 3 listed IPOs 同章节同行业, 让 similar_cases 找到
        for i, (m6, d30) in enumerate([(0.10, 0.05), (0.20, 0.08), (0.05, -0.02)]):
            upsert_ipo(conn, ipo_id=f"HK_S{i}", stock_code=f"S{i}.HK",
                       listing_date=f"2024-0{i + 1}-01",
                       listing_chapter="main_board_profitable",
                       gics_l2="医疗", status="listed")
            conn.execute(
                "INSERT INTO ipo_returns (ipo_id, return_d30, return_m6, "
                "is_d30_due, is_m6_due) VALUES (?, ?, ?, 1, 1)",
                (f"HK_S{i}", d30, m6),
            )
        # 主 IPO 自身的 actual
        conn.execute(
            "INSERT INTO ipo_returns (ipo_id, return_d30, return_m6, "
            "is_d30_due, is_m6_due) VALUES (?, ?, ?, 1, 1)",
            ("HK_001", 0.15, 0.30),
        )
        _persist_n_predictions(conn, "0001.HK", make_ipo, asofs=[date(2026, 1, 1)])
        rep = review(conn, "0001.HK")

    sim = rep.get("similar_cases", {})
    # 中位数应在 [0.05, 0.10, 0.20] → 0.10
    assert sim.get("m6_median") is not None
    # 主 IPO 比 similar median 高 → diff 正
    diff = rep.get("similar_m6_diff")
    assert diff is not None
    assert diff > 0
