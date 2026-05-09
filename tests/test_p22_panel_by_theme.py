"""
P2.2 — panel.aggregates 按 theme_id 分桶测试.

新行为: compute_panel_aggregates(conn, theme_definitions=...) 多算 by_theme 桶.
跟 by_gics_l2 同样的 ≥5 样本门槛.

覆盖:
    - theme_definitions=None → by_theme 空 (向后兼容)
    - theme_definitions 提供 + ≥5 个同主题 listed IPO → by_theme 桶生成
    - 同主题 IPO <5 → 不进 by_theme (噪音不收录)
    - core_companies 命中 → 强信号分类
    - GICS / concept 命中 → 弱信号分类
    - 都没命中 → 不进任何桶
    - by_theme 跟 by_chapter / by_gics_l2 / overall 共存
    - write_panel_snapshot 透传 theme_definitions 到 aggregates
    - aggregates_json 落库后能读出 by_theme 桶
"""
from __future__ import annotations

import json
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


def _seed_concept(conn, ipo_id, stock_code, concept_name):
    conn.execute(
        "INSERT INTO ipo_concepts (ipo_id, stock_code, concept_id, concept_name) "
        "VALUES (?, ?, ?, ?)",
        (ipo_id, stock_code, concept_name, concept_name),
    )


def _theme_defs_minimal():
    """伪 theme_definitions: 单主题 'ai_server', core_companies 含 0001-0005"""
    return {
        "_schema_version": "1.0",
        "themes": {
            "ai_server": {
                "label": "AI 服务器",
                "core_companies": [
                    {"code": "1.HK",  "name": "AI 服务器 #1"},
                    {"code": "2.HK",  "name": "AI 服务器 #2"},
                    {"code": "3.HK",  "name": "AI 服务器 #3"},
                    {"code": "4.HK",  "name": "AI 服务器 #4"},
                    {"code": "5.HK",  "name": "AI 服务器 #5"},
                ],
                "keywords": ["AI 服务器", "GPU", "推理加速"],
                "exclude": [],
            },
            "innovative_drug": {
                "label": "创新药",
                "core_companies": [
                    {"code": "100.HK", "name": "创新药 #1"},
                ],
                "keywords": ["PD-1", "ADC", "创新药"],
                "exclude": [],
            },
        },
    }


# =============================================================================
# compute_panel_aggregates by_theme bucket
# =============================================================================

class TestByThemeBucket:
    def test_no_theme_defs_no_bucket(self, empty_db):
        """theme_definitions=None → by_theme 字典为空 (向后兼容)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed", pe_at_offer=15.0)
            aggs = compute_panel_aggregates(conn)
        assert aggs["by_theme"] == {}

    def test_core_company_match_creates_bucket(self, empty_db):
        """5 个 core_companies 列表里的 IPO → by_theme['ai_server'] 桶"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed", pe_at_offer=15.0)
            aggs = compute_panel_aggregates(conn, theme_definitions=_theme_defs_minimal())
        assert "ai_server" in aggs["by_theme"]
        assert aggs["by_theme"]["ai_server"]["n"] == 5
        assert aggs["by_theme"]["ai_server"]["pe_at_offer_p50"] == 15.0

    def test_min_5_samples_per_theme(self, empty_db):
        """同一主题 4 只 IPO → 不进 by_theme (跟 by_gics_l2 同 ≥5 门槛)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            # 4 只 ai_server, 应该被丢弃
            for i in range(1, 5):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed", pe_at_offer=15.0)
            aggs = compute_panel_aggregates(conn, theme_definitions=_theme_defs_minimal())
        assert "ai_server" not in aggs["by_theme"]
        # by_chapter 仍有 (不受 ≥5 门槛限制)
        assert "main_board_profitable" in aggs["by_chapter"]

    def test_concept_match_picks_up_theme(self, empty_db):
        """ipo_concepts 含 'GPU' 关键词 → 命中 ai_server (弱匹配)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            # 5 只非 core_companies 的 IPO, 但都有 ipo_concepts='GPU'
            for i in range(50, 55):
                ipo_id = f"HK_{i:03d}"
                stock_code = f"{i:04d}.HK"
                _seed_master(conn, ipo_id, stock_code,
                             "2024-01-01", "listed", pe_at_offer=20.0)
                _seed_concept(conn, ipo_id, stock_code, "GPU")
            aggs = compute_panel_aggregates(conn, theme_definitions=_theme_defs_minimal())
        assert "ai_server" in aggs["by_theme"]
        assert aggs["by_theme"]["ai_server"]["n"] == 5

    def test_unmatched_dropped(self, empty_db):
        """都不匹配的 IPO 不进 by_theme 任何桶"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            for i in range(900, 906):    # 6 只 IPO, 跟任何主题都无关
                _seed_master(conn, f"HK_{i}", f"{i}.HK",
                             "2024-01-01", "listed", pe_at_offer=12.0)
            aggs = compute_panel_aggregates(conn, theme_definitions=_theme_defs_minimal())
        # by_theme 空 (没主题命中)
        assert aggs["by_theme"] == {}
        # 但 overall 仍统计了所有 6 只
        assert aggs["overall"]["n"] == 6

    def test_buckets_coexist_with_chapter_and_gics(self, empty_db):
        """by_theme 不替换 by_chapter / by_gics_l2 / overall, 平行共存"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            # 5 只 ai_server core_companies (强信号)
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed",
                             pe_at_offer=15.0,
                             gics_l2="资讯科技业(HS)-硬件",
                             listing_chapter="main_board_profitable")
            # 加 5 只 GICS 重叠样本以让 by_gics 触发 ≥5
            for i in range(20, 25):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed",
                             pe_at_offer=22.0,
                             gics_l2="资讯科技业(HS)-硬件",
                             listing_chapter="main_board_profitable")
            aggs = compute_panel_aggregates(conn, theme_definitions=_theme_defs_minimal())
        assert "by_theme" in aggs
        assert "by_chapter" in aggs
        assert "by_gics_l2" in aggs
        assert "overall" in aggs
        assert "ai_server" in aggs["by_theme"]
        assert "main_board_profitable" in aggs["by_chapter"]
        assert aggs["overall"]["n"] == 10


# =============================================================================
# write_panel_snapshot 透传
# =============================================================================

class TestWriteSnapshotPassesThroughThemes:
    def test_snapshot_persists_by_theme(self, empty_db):
        """write_panel_snapshot(theme_definitions=...) 后 aggregates_json 含 by_theme"""
        from data.dao import db_connect
        from data.panel_snapshot import write_panel_snapshot
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed", pe_at_offer=15.0)
            sid = write_panel_snapshot(
                conn, asof=date(2026, 5, 9),
                market_env={}, regime_score=None,
                config_dict={"version": "v8"},
                theme_definitions=_theme_defs_minimal(),
            )
            row = conn.execute(
                "SELECT aggregates_json FROM panel_snapshots WHERE snapshot_id=?",
                (sid,),
            ).fetchone()
        aggs = json.loads(row[0])
        assert "by_theme" in aggs
        assert "ai_server" in aggs["by_theme"]
        assert aggs["by_theme"]["ai_server"]["n"] == 5

    def test_snapshot_without_themes_back_compat(self, empty_db):
        """不传 theme_definitions → by_theme 仍生成空 dict, 不报错"""
        from data.dao import db_connect
        from data.panel_snapshot import write_panel_snapshot
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i:03d}", f"{i:04d}.HK",
                             "2024-01-01", "listed", pe_at_offer=15.0)
            sid = write_panel_snapshot(
                conn, asof=date(2026, 5, 9),
                market_env={}, regime_score=None,
                config_dict={"version": "v8"},
            )
            row = conn.execute(
                "SELECT aggregates_json FROM panel_snapshots WHERE snapshot_id=?",
                (sid,),
            ).fetchone()
        aggs = json.loads(row[0])
        assert aggs.get("by_theme") == {}
