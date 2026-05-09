"""
nacs_predictions 落盘 + 同伴比对 + 查询测试.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest


# =============================================================================
# persist_prediction
# =============================================================================

def test_persist_prediction_full_roundtrip(empty_db, make_ipo):
    """走通 compute_nacs → persist → 查询; 各 JSON 字段都能反序列化"""
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot
    from data.predictions import persist_prediction, get_prediction
    from nacs_model import compute_nacs

    with db_connect(str(empty_db)) as conn:
        snap_id = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={"hsi_60d_return": 0.03},
            regime_score=0.04,
            config_dict={"version": "v8"},
        )
        offering = make_ipo()
        result = compute_nacs(offering)
        case_id = persist_prediction(
            conn,
            result=result, offering=offering,
            stock_code="0001.HK",
            asof=date(2026, 5, 9),
            panel_snapshot_id=snap_id,
            deal_status_at_analysis="prospectus",
            price_scenario="mid",
            offer_price_used=8.0,
            notes="test case",
        )
        rec = get_prediction(conn, case_id)

    assert rec is not None
    assert rec["case_id"] == case_id
    assert rec["stock_code"] == "0001.HK"
    assert rec["price_scenario"] == "mid"
    assert rec["panel_snapshot_id"] == snap_id
    assert rec["nacs_adjusted"] == result.nacs_adjusted
    assert rec["decision"] == result.decision

    l1 = json.loads(rec["layer1_components_json"])
    assert "L1.1_valuation" in l1            # 构造时只有这些 keys
    inputs = json.loads(rec["inputs_json"])
    assert inputs["company_name"] == "Test"
    sim = json.loads(rec["similar_cases_json"])
    assert isinstance(sim, list)


def test_persist_idempotent_same_key(empty_db, make_ipo):
    """同 (stock, asof, scenario, snap) 重复 persist 是 update 不是新行"""
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot
    from data.predictions import persist_prediction
    from nacs_model import compute_nacs

    with db_connect(str(empty_db)) as conn:
        snap_id = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=0.0,
            config_dict={"version": "v8"},
        )
        offering = make_ipo()
        result = compute_nacs(offering)
        c1 = persist_prediction(conn, result=result, offering=offering,
                                stock_code="0001.HK", asof=date(2026, 5, 9),
                                panel_snapshot_id=snap_id,
                                price_scenario="mid")
        c2 = persist_prediction(conn, result=result, offering=offering,
                                stock_code="0001.HK", asof=date(2026, 5, 9),
                                panel_snapshot_id=snap_id,
                                price_scenario="mid",
                                notes="updated note")
        n = conn.execute(
            "SELECT COUNT(*) FROM nacs_predictions WHERE stock_code='0001.HK'"
        ).fetchone()[0]
        notes = conn.execute(
            "SELECT notes FROM nacs_predictions WHERE case_id=?", (c1,)
        ).fetchone()[0]
    assert c1 == c2
    assert n == 1
    assert notes == "updated note"


def test_different_scenarios_create_different_cases(empty_db, make_ipo):
    """price_scenario low/mid/high 各走一行"""
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot
    from data.predictions import persist_prediction
    from nacs_model import compute_nacs

    with db_connect(str(empty_db)) as conn:
        snap_id = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=0.0,
            config_dict={"version": "v8"},
        )
        offering = make_ipo()
        result = compute_nacs(offering)
        cases = []
        for scenario in ("low", "mid", "high"):
            cases.append(persist_prediction(
                conn, result=result, offering=offering,
                stock_code="0001.HK", asof=date(2026, 5, 9),
                panel_snapshot_id=snap_id, price_scenario=scenario,
            ))
        n = conn.execute("SELECT COUNT(*) FROM nacs_predictions").fetchone()[0]
    assert len(set(cases)) == 3
    assert n == 3


# =============================================================================
# similar_cases
# =============================================================================

def test_similar_cases_finds_same_chapter_same_gics(empty_db):
    """构造一个 panel: 同章节同行业的应排第一"""
    from data.dao import db_connect, upsert_ipo
    from data.predictions import find_similar_cases

    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_A", stock_code="A.HK",
                   listing_date="2024-06-01", listing_chapter="main_board_profitable",
                   gics_l2="医疗设备", status="listed")
        upsert_ipo(conn, ipo_id="HK_B", stock_code="B.HK",
                   listing_date="2024-08-01", listing_chapter="main_board_profitable",
                   gics_l2="科技", status="listed")
        upsert_ipo(conn, ipo_id="HK_C", stock_code="C.HK",
                   listing_date="2024-10-01", listing_chapter="18a",
                   gics_l2="医疗设备", status="listed")
        upsert_ipo(conn, ipo_id="HK_D_PROSPECTUS", stock_code="D.HK",
                   listing_date="2026-12-01", listing_chapter="main_board_profitable",
                   gics_l2="医疗设备", status="prospectus")

        sims = find_similar_cases(
            conn, chapter="main_board_profitable", gics_l2="医疗设备",
            q_company=0.7, q_ecosystem=0.5, r_lockup=0.2,
            min_listing_date="2023-01-01",
        )
    ipo_ids = [s["ipo_id"] for s in sims]
    # A 同 chapter + 同 gics → 第一; D 是 prospectus 不应入选; B/C 部分 match
    assert "HK_A" in ipo_ids
    assert "HK_D_PROSPECTUS" not in ipo_ids
    assert sims[0]["ipo_id"] == "HK_A"
    assert sims[0]["match_dims"] == ["chapter", "gics_l2"]


def test_similar_cases_actual_returns_filtered_by_due_flag(empty_db):
    """is_d30_due=0 的样本 actual_d30 应是 None"""
    from data.dao import db_connect, upsert_ipo
    from data.predictions import find_similar_cases

    with db_connect(str(empty_db)) as conn:
        upsert_ipo(conn, ipo_id="HK_DUE", stock_code="X.HK",
                   listing_date="2024-01-01", listing_chapter="main_board_profitable",
                   gics_l2="科技", status="listed")
        conn.execute(
            "INSERT INTO ipo_returns (ipo_id, return_d30, is_d30_due, return_m6, is_m6_due) "
            "VALUES (?, ?, ?, ?, ?)",
            ("HK_DUE", 0.10, 1, 0.20, 1)
        )
        upsert_ipo(conn, ipo_id="HK_NOTDUE", stock_code="Y.HK",
                   listing_date="2026-04-01", listing_chapter="main_board_profitable",
                   gics_l2="科技", status="listed")
        conn.execute(
            "INSERT INTO ipo_returns (ipo_id, return_d30, is_d30_due, return_m6, is_m6_due) "
            "VALUES (?, ?, ?, ?, ?)",
            ("HK_NOTDUE", None, 0, None, 0)
        )
        sims = find_similar_cases(
            conn, chapter="main_board_profitable", gics_l2="科技",
            q_company=0.5, q_ecosystem=0.5, r_lockup=0.2,
            min_listing_date="2022-01-01",
        )
    by_id = {s["ipo_id"]: s for s in sims}
    assert by_id["HK_DUE"]["actual_d30"] == 0.10
    assert by_id["HK_NOTDUE"]["actual_d30"] is None


# =============================================================================
# Lookups
# =============================================================================

def test_list_predictions_for_stock_orders_by_asof(empty_db, make_ipo):
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot
    from data.predictions import persist_prediction, list_predictions_for_stock
    from nacs_model import compute_nacs

    with db_connect(str(empty_db)) as conn:
        offering = make_ipo()
        result = compute_nacs(offering)
        for asof in [date(2026, 1, 1), date(2025, 11, 1), date(2026, 4, 1)]:
            sid = write_panel_snapshot(
                conn, asof=asof,
                market_env={}, regime_score=0.0,
                config_dict={"version": "v8", "_d": asof.isoformat()},
            )
            persist_prediction(conn, result=result, offering=offering,
                               stock_code="0001.HK", asof=asof,
                               panel_snapshot_id=sid, price_scenario="mid")
        preds = list_predictions_for_stock(conn, "0001.HK")
    assert len(preds) == 3
    asofs = [str(p["asof_date"])[:10] for p in preds]
    assert asofs == sorted(asofs)
