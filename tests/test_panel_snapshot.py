"""
panel_snapshot 模块测试.

覆盖:
    - write_panel_snapshot 在空 panel 上能成功 (n=0, aggregates 空各字段)
    - 多 IPO + status 过滤: prospectus 行不进 panel
    - aggregates 按章节分组
    - get_latest 返回最新一行
    - 同 (asof, cfg_hash) 重复写是 update (不产生重复)
    - market_env / regime_score 落 JSON
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest


# =============================================================================
# Helpers
# =============================================================================

def _seed_master(conn, ipo_id, code, listing_date, status, **kwargs):
    from data.dao import upsert_ipo
    upsert_ipo(conn, ipo_id=ipo_id, stock_code=code,
               listing_date=listing_date,
               listing_chapter=kwargs.pop("listing_chapter", "main_board_profitable"),
               status=status, **kwargs)


def _seed_returns(conn, ipo_id, **kwargs):
    cols = ["ipo_id"] + list(kwargs.keys())
    vals = [ipo_id] + list(kwargs.values())
    conn.execute(
        f"INSERT INTO ipo_returns ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})",
        vals,
    )


# =============================================================================
# Tests
# =============================================================================

def test_write_snapshot_empty_panel(empty_db):
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot, get_latest_panel_snapshot

    with db_connect(str(empty_db)) as conn:
        sid = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={"hsi_60d_return": 0.03},
            regime_score=0.05,
            config_dict={"version": "v8"},
        )
        snap = get_latest_panel_snapshot(conn)
    assert sid.startswith("PANEL_2026-05-09_")
    assert snap["snapshot_id"] == sid
    assert snap["n_ipos_in_universe"] == 0
    assert json.loads(snap["member_ipo_ids_json"]) == []
    assert json.loads(snap["market_env_json"])["hsi_60d_return"] == 0.03
    assert snap["regime_score"] == 0.05


def test_panel_only_listed_status(empty_db):
    """status='prospectus' 的行不应进 panel"""
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot

    with db_connect(str(empty_db)) as conn:
        _seed_master(conn, "HK_001", "0001.HK", "2024-01-01", "listed",
                     pe_at_offer=15.0)
        _seed_master(conn, "HK_002", "0002.HK", "2024-06-01", "listed",
                     pe_at_offer=20.0)
        _seed_master(conn, "HK_003", "0003.HK", "2026-12-01", "prospectus")
        _seed_master(conn, "HK_004", "0004.HK", "2024-03-01", "delisted")

        sid = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={"hsi_60d_return": 0.0},
            regime_score=0.0,
            config_dict={"version": "v8"},
        )
        snap = conn.execute(
            "SELECT n_ipos_in_universe, member_ipo_ids_json FROM panel_snapshots "
            "WHERE snapshot_id=?", (sid,)
        ).fetchone()
    assert snap[0] == 2
    assert set(json.loads(snap[1])) == {"HK_001", "HK_002"}


def test_aggregates_compute_per_chapter(empty_db):
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot

    with db_connect(str(empty_db)) as conn:
        # 5 main_board IPO (PE 5..25), 2 18a IPO (PE 30, 40)
        for i, pe in enumerate([5.0, 10.0, 15.0, 20.0, 25.0]):
            _seed_master(conn, f"HK_M{i}", f"M{i:04d}.HK", "2024-01-01",
                         "listed", listing_chapter="main_board_profitable",
                         pe_at_offer=pe)
        for i, pe in enumerate([30.0, 40.0]):
            _seed_master(conn, f"HK_A{i}", f"A{i:04d}.HK", "2024-01-01",
                         "listed", listing_chapter="18a", pe_at_offer=pe)

        sid = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=None,
            config_dict={"version": "v8"},
        )
        snap = conn.execute(
            "SELECT aggregates_json FROM panel_snapshots WHERE snapshot_id=?",
            (sid,)
        ).fetchone()

    aggs = json.loads(snap[0])
    assert "main_board_profitable" in aggs["by_chapter"]
    assert aggs["by_chapter"]["main_board_profitable"]["n"] == 5
    assert aggs["by_chapter"]["main_board_profitable"]["pe_at_offer_p50"] == 15.0
    # 18a 只有 2 个, 不会进 by_gics_l2 (>=5 才进), 但 by_chapter 不限.
    assert aggs["by_chapter"]["18a"]["n"] == 2
    assert aggs["overall"]["n"] == 7


def test_due_filter_applied_to_aggregates(empty_db):
    """is_m6_due=0 的行不应进 m6 中位数"""
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot

    with db_connect(str(empty_db)) as conn:
        # 3 个 due 的, 2 个 not due 的 (extreme value)
        for i, (m6, due) in enumerate([
            (0.10, 1), (0.20, 1), (0.30, 1),
            (5.0, 0), (-5.0, 0),                # 假极值, 不应入中位
        ]):
            _seed_master(conn, f"HK_{i}", f"{i:04d}.HK", "2024-01-01", "listed",
                         pe_at_offer=10.0)
            _seed_returns(conn, f"HK_{i}", return_m6=m6, is_m6_due=due,
                          is_d30_due=1)
        sid = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=None,
            config_dict={"version": "v8"},
        )
        aggs = json.loads(conn.execute(
            "SELECT aggregates_json FROM panel_snapshots WHERE snapshot_id=?",
            (sid,)
        ).fetchone()[0])
    overall = aggs["overall"]
    # due-filtered: median of [0.10, 0.20, 0.30] = 0.20
    assert overall["return_m6_p50"] == 0.20


def test_idempotent_same_asof_and_cfg(empty_db):
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot

    with db_connect(str(empty_db)) as conn:
        sid1 = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=0.0,
            config_dict={"version": "v8"},
        )
        sid2 = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=0.05,    # 改了 regime
            config_dict={"version": "v8"},
        )
        n = conn.execute("SELECT COUNT(*) FROM panel_snapshots").fetchone()[0]
        # 两个 snapshot_id 相等 (因为同 asof + 同 cfg_hash)
        assert sid1 == sid2
        assert n == 1
        # update 应反映新 regime_score
        regime = conn.execute(
            "SELECT regime_score FROM panel_snapshots WHERE snapshot_id=?", (sid1,)
        ).fetchone()[0]
    assert regime == 0.05


def test_get_latest_returns_most_recent(empty_db):
    from data.dao import db_connect
    from data.panel_snapshot import write_panel_snapshot, get_latest_panel_snapshot

    with db_connect(str(empty_db)) as conn:
        write_panel_snapshot(conn, asof=date(2026, 5, 1),
                             market_env={}, regime_score=0.0,
                             config_dict={"version": "v8"})
        sid_latest = write_panel_snapshot(
            conn, asof=date(2026, 5, 9),
            market_env={}, regime_score=0.0,
            config_dict={"version": "v8"})
        snap = get_latest_panel_snapshot(conn)
    assert snap["snapshot_id"] == sid_latest
