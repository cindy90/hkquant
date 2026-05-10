"""
P3.2.A — panel ps_at_offer 计算 + lookup_ps_peer_median 测试.

新行为:
    compute_panel_aggregates 多算 ps_at_offer (mkt_cap_HKD / revenue_HKD) 并
    bucket 成 ps_at_offer_p25/p50/p75 + ps_at_offer_n.

    lookup_ps_peer_median(aggs, theme_id, listing_chapter, min_sample) cascade
    by_theme → by_chapter → overall, 任一层 ps_p50=None 或 n<min_sample 跳下层.

覆盖:
    - panel: 缺 revenue / shares / price 的 row 不入 ps 样本但仍入 pe 样本
    - panel: ps_at_offer_p50 跨 by_chapter / by_gics / by_theme / overall 都有
    - panel: fx_cny_hkd 影响 ps 数值 (默认 1.10)
    - lookup: by_theme 命中 (n≥min) 优先返回, source="theme:xxx"
    - lookup: by_theme 缺 / 样本不足 → fall to by_chapter
    - lookup: by_chapter 也无 → fall to overall
    - lookup: 三层都无 → (None, None)
    - lookup: aggregates=None 安全返回 (None, None)
"""
from __future__ import annotations

import json
from datetime import date

import pytest


# =============================================================================
# Helpers (跟 P2.2 panel test 用同样的 fixture pattern)
# =============================================================================

def _seed_master(conn, ipo_id, code, listing_date, status, **kwargs):
    from data.dao import upsert_ipo
    upsert_ipo(conn, ipo_id=ipo_id, stock_code=code,
               listing_date=listing_date,
               listing_chapter=kwargs.pop("listing_chapter", "main_board_profitable"),
               status=status, **kwargs)


def _seed_revenue(conn, stock_code, year, revenue_cny):
    conn.execute(
        "INSERT INTO ipo_financials (stock_code, report_year, revenue_cny) "
        "VALUES (?, ?, ?)",
        (stock_code, year, revenue_cny),
    )


def _theme_defs_minimal():
    return {
        "_schema_version": "1.0",
        "themes": {
            "ai_server": {
                "label": "AI 服务器",
                "core_companies": [
                    {"code": f"{i}.HK", "name": f"AI #{i}"} for i in range(1, 8)
                ],
                "keywords": ["AI 服务器", "GPU"],
                "exclude": [],
            },
        },
    }


# =============================================================================
# panel ps_at_offer 计算
# =============================================================================

class TestPanelPSAggregate:
    def test_ps_computed_per_row(self, empty_db):
        """每只 listed IPO 算 ps = (post_ipo_shares × offer_price) / (rev × fx)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            # 5 个 IPO, mkt_cap=10B, revenue=2B CNY → revenue_HKD=2.2B → ps=4.55
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i}", f"{i:04d}.HK",
                             "2024-01-01", "listed",
                             post_ipo_shares=1_000_000_000,
                             offer_price_hkd=10.0,
                             pe_at_offer=15.0)
                _seed_revenue(conn, f"{i:04d}.HK", 2023, 2_000_000_000)
            aggs = compute_panel_aggregates(conn)
        overall = aggs["overall"]
        assert overall["ps_at_offer_n"] == 5
        # mkt_cap=10B, revenue_HKD=2B*1.10=2.2B → ps=4.545
        assert overall["ps_at_offer_p50"] == pytest.approx(10e9 / (2e9 * 1.10), abs=0.01)

    def test_missing_revenue_excluded_from_ps_but_kept_for_pe(self, empty_db):
        """无 revenue 的 row 不入 ps_at_offer 样本, 但 pe_at_offer 仍计入"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            # 5 个 IPO 都有 PE=15; 只有 3 个有 revenue
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i}", f"{i:04d}.HK",
                             "2024-01-01", "listed",
                             post_ipo_shares=1_000_000_000,
                             offer_price_hkd=10.0,
                             pe_at_offer=15.0)
            for i in range(1, 4):
                _seed_revenue(conn, f"{i:04d}.HK", 2023, 2_000_000_000)
            aggs = compute_panel_aggregates(conn)
        overall = aggs["overall"]
        # PE 5 个全有
        assert overall["pe_at_offer_p50"] == 15.0
        # PS 只 3 个
        assert overall["ps_at_offer_n"] == 3

    def test_missing_shares_or_price_excluded(self, empty_db):
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            # 1 全, 2 缺 shares, 3 缺 price, 4-5 全
            _seed_master(conn, "HK_1", "0001.HK", "2024-01-01", "listed",
                         post_ipo_shares=1e9, offer_price_hkd=10.0)
            _seed_revenue(conn, "0001.HK", 2023, 2e9)
            _seed_master(conn, "HK_2", "0002.HK", "2024-01-01", "listed",
                         post_ipo_shares=None, offer_price_hkd=10.0)
            _seed_revenue(conn, "0002.HK", 2023, 2e9)
            _seed_master(conn, "HK_3", "0003.HK", "2024-01-01", "listed",
                         post_ipo_shares=1e9, offer_price_hkd=None)
            _seed_revenue(conn, "0003.HK", 2023, 2e9)
            for i in (4, 5):
                _seed_master(conn, f"HK_{i}", f"{i:04d}.HK", "2024-01-01",
                             "listed", post_ipo_shares=1e9, offer_price_hkd=10.0)
                _seed_revenue(conn, f"{i:04d}.HK", 2023, 2e9)
            aggs = compute_panel_aggregates(conn)
        # 1, 4, 5 入 ps; 2, 3 缺数据
        assert aggs["overall"]["ps_at_offer_n"] == 3

    def test_revenue_zero_excluded(self, empty_db):
        """revenue=0 → 不算 ps (无意义除零)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            _seed_master(conn, "HK_1", "0001.HK", "2024-01-01", "listed",
                         post_ipo_shares=1e9, offer_price_hkd=10.0)
            _seed_revenue(conn, "0001.HK", 2023, 0.0)
            aggs = compute_panel_aggregates(conn)
        # WHERE revenue_cny IS NOT NULL 但 0 应该被过滤
        # (compute_panel_aggregates 内部 IS NOT NULL + > 0 双重保护)
        assert aggs["overall"]["ps_at_offer_n"] == 0

    def test_fx_rate_applied(self, empty_db):
        """fx_cny_hkd=2.0 时 ps 应是默认 1.10 的 1/2 (revenue 翻倍)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i}", f"{i:04d}.HK",
                             "2024-01-01", "listed",
                             post_ipo_shares=1e9, offer_price_hkd=10.0)
                _seed_revenue(conn, f"{i:04d}.HK", 2023, 2e9)
            agg_default = compute_panel_aggregates(conn)
            agg_double = compute_panel_aggregates(conn, fx_cny_hkd=2.0)
        # ps_default = 10B / (2B * 1.10) = 4.545
        # ps_double  = 10B / (2B * 2.00) = 2.50
        assert agg_default["overall"]["ps_at_offer_p50"] == pytest.approx(4.545, abs=0.01)
        assert agg_double["overall"]["ps_at_offer_p50"] == pytest.approx(2.50, abs=0.01)

    def test_multi_year_picks_latest(self, empty_db):
        """ipo_financials 多年数据 → 取 MAX(report_year)"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 6):
                _seed_master(conn, f"HK_{i}", f"{i:04d}.HK",
                             "2024-01-01", "listed",
                             post_ipo_shares=1e9, offer_price_hkd=10.0)
                _seed_revenue(conn, f"{i:04d}.HK", 2021, 1e9)
                _seed_revenue(conn, f"{i:04d}.HK", 2022, 1.5e9)
                _seed_revenue(conn, f"{i:04d}.HK", 2023, 2e9)   # latest
            aggs = compute_panel_aggregates(conn)
        # 最近一年 = 2023, revenue=2B → ps=10B/(2B*1.10)=4.545
        assert aggs["overall"]["ps_at_offer_p50"] == pytest.approx(4.545, abs=0.01)

    def test_ps_in_by_theme_bucket(self, empty_db):
        """theme_definitions 提供 + 5 个 core_companies → by_theme[theme].ps_p50 计算"""
        from data.dao import db_connect
        from data.panel_snapshot import compute_panel_aggregates
        with db_connect(str(empty_db)) as conn:
            for i in range(1, 8):
                _seed_master(conn, f"HK_{i}", f"{i}.HK", "2024-01-01", "listed",
                             post_ipo_shares=1e9, offer_price_hkd=10.0,
                             pe_at_offer=15.0)
                _seed_revenue(conn, f"{i}.HK", 2023, 2e9)
            aggs = compute_panel_aggregates(conn,
                                            theme_definitions=_theme_defs_minimal())
        assert "ai_server" in aggs["by_theme"]
        assert aggs["by_theme"]["ai_server"]["ps_at_offer_n"] == 7
        assert aggs["by_theme"]["ai_server"]["ps_at_offer_p50"] == pytest.approx(
            4.545, abs=0.01)


# =============================================================================
# lookup_ps_peer_median cascade
# =============================================================================

class TestLookupPSPeerMedian:
    def _aggs_full(self):
        """工厂: 三层都有数据"""
        return {
            "by_theme": {
                "ai_server": {
                    "ps_at_offer_n": 8, "ps_at_offer_p50": 7.0,
                },
                "innovative_drug": {
                    "ps_at_offer_n": 3,    # 不足 min_sample=5
                    "ps_at_offer_p50": 12.0,
                },
            },
            "by_chapter": {
                "main_board_profitable": {
                    "ps_at_offer_n": 50, "ps_at_offer_p50": 4.5,
                },
                "18c_commercial": {
                    "ps_at_offer_n": 6, "ps_at_offer_p50": 9.5,
                },
            },
            "overall": {"ps_at_offer_n": 100, "ps_at_offer_p50": 5.5},
        }

    def test_theme_match_returns_theme(self):
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(
            self._aggs_full(),
            theme_id="ai_server",
            listing_chapter="18c_commercial",
        )
        assert ps == 7.0
        assert src == "theme:ai_server"

    def test_theme_below_sample_falls_to_chapter(self):
        """innovative_drug 只有 3 个样本 < min_sample → fall to chapter"""
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(
            self._aggs_full(),
            theme_id="innovative_drug",
            listing_chapter="18c_commercial",
        )
        assert ps == 9.5
        assert src == "chapter:18c_commercial"

    def test_theme_missing_falls_to_chapter(self):
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(
            self._aggs_full(),
            theme_id="some_unknown",
            listing_chapter="main_board_profitable",
        )
        assert ps == 4.5
        assert src == "chapter:main_board_profitable"

    def test_theme_none_falls_to_chapter(self):
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(
            self._aggs_full(),
            theme_id=None,
            listing_chapter="18c_commercial",
        )
        assert ps == 9.5
        assert src == "chapter:18c_commercial"

    def test_chapter_missing_falls_to_overall(self):
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(
            self._aggs_full(),
            theme_id=None,
            listing_chapter="some_unknown_chapter",
        )
        assert ps == 5.5
        assert src == "overall"

    def test_all_layers_missing_returns_none(self):
        from data.panel_snapshot import lookup_ps_peer_median
        empty_aggs = {"by_theme": {}, "by_chapter": {}, "overall": {}}
        ps, src = lookup_ps_peer_median(
            empty_aggs, theme_id=None, listing_chapter=None,
        )
        assert ps is None
        assert src is None

    def test_aggregates_none_safe(self):
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(None, theme_id="ai_server")
        assert ps is None
        assert src is None

    def test_overall_below_sample_returns_none(self):
        """overall n < min_sample → None"""
        from data.panel_snapshot import lookup_ps_peer_median
        aggs = {
            "by_theme": {},
            "by_chapter": {},
            "overall": {"ps_at_offer_n": 2, "ps_at_offer_p50": 5.5},
        }
        ps, src = lookup_ps_peer_median(aggs, theme_id=None, min_sample=5)
        assert ps is None
        assert src is None

    def test_custom_min_sample(self):
        """改 min_sample=3 → innovative_drug (n=3) 应触发"""
        from data.panel_snapshot import lookup_ps_peer_median
        ps, src = lookup_ps_peer_median(
            self._aggs_full(),
            theme_id="innovative_drug",
            listing_chapter="some_chapter",
            min_sample=3,
        )
        assert ps == 12.0
        assert src == "theme:innovative_drug"
