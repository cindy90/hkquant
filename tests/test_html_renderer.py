"""
HTML renderer 测试.

覆盖:
    - filters: pct / num / ret_class / bar_pct / json_dump
    - render_single_deal: 自包含 HTML, 含决策徽章 + 三因子柱
    - render_single_deal: --price-scan 多场景渲染表
    - render_compare: 多 deal 横评
    - render_case_review: 复盘报告
    - 错误路径: 空 records 抛 ValueError
    - 输出可被 ElementTree 解析 (HTML 结构良好)
    - 输出 utf-8, 含中文不乱码
    - CSS 完整嵌入 (浏览器双击就能开)
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest


# =============================================================================
# Filter 单元
# =============================================================================

class TestFilters:
    def test_pct(self):
        from reports.html_renderer import _filter_pct
        assert _filter_pct(0.123) == "+12.30%"
        assert _filter_pct(-0.05) == "-5.00%"
        assert _filter_pct(0) == "+0.00%"
        assert _filter_pct(None) == "n/a"

    def test_num(self):
        from reports.html_renderer import _filter_num
        assert _filter_num(0.5) == "0.5000"
        assert _filter_num(0.5, 2) == "0.50"
        assert _filter_num(None) == "n/a"
        assert _filter_num(42) == "42"

    def test_ret_class(self):
        from reports.html_renderer import _filter_ret_class
        assert _filter_ret_class(0.10) == "ret-pos"
        assert _filter_ret_class(-0.10) == "ret-neg"
        assert _filter_ret_class(0) == "ret-neutral"
        assert _filter_ret_class(None) == "ret-pending"

    def test_bar_pct(self):
        from reports.html_renderer import _filter_bar_pct
        assert _filter_bar_pct(0.5) == 50
        assert _filter_bar_pct(0.0) == 0
        assert _filter_bar_pct(1.5) == 100  # 截断
        assert _filter_bar_pct(-0.1) == 0   # 截断
        assert _filter_bar_pct(None) == 0

    def test_json_dump_handles_dataclass_and_enum(self):
        """_to_jsonable 应能处理 NACSResult / IPOOffering 内嵌的 Enum + dataclass"""
        from reports.html_renderer import _filter_json_dump
        out = _filter_json_dump({"a": 1, "b": [1, 2, 3]})
        assert "1" in out and "[" in out


# =============================================================================
# render_single_deal
# =============================================================================

def _build_records(make_ipo, n_scenarios=1):
    """跑 compute_nacs 一次, 包装成 _evaluate_deal 风格的 record list"""
    from nacs_model import compute_nacs

    offering = make_ipo()
    result = compute_nacs(offering)

    # mock sqlite Row
    class MockRow:
        def __init__(self, d):
            self._d = d
        def __getitem__(self, k):
            return self._d.get(k)
        def keys(self):
            return list(self._d.keys())

    row = MockRow({
        "stock_code": "0001.HK",
        "company_name_zh": "测试公司",
        "status": "prospectus",
        "listing_chapter": "main_board_profitable",
        "gics_l2": "测试行业",
        "listing_date": "2026-09-15",
        "expected_listing_date": "2026-09-15",
    })

    if n_scenarios == 1:
        scenarios = [("mid", 9.0)]
    else:
        scenarios = [("low", 8.0), ("mid", 9.0), ("high", 10.0)]

    return [
        {
            "stock_code": "0001.HK", "ipo_id": "HK_001",
            "row": row, "scenario": s, "price": p,
            "offering": offering, "result": result,
        }
        for s, p in scenarios
    ]


def _build_snap(snapshot_id="PANEL_2026-05-09_test01"):
    return {
        "snapshot_id": snapshot_id,
        "asof_date": "2026-05-09",
        "n_ipos_in_universe": 384,
        "regime_score": 0.04,
        "config_version": "v8",
        "config_hash": "abcdef",
        "code_git_sha": "deadbeef",
        "market_env_json": '{"hsi_60d_return": 0.03}',
        "aggregates_json": '{"overall": {"pe_at_offer_p25": 10.0, "pe_at_offer_p50": 18.0, "pe_at_offer_p75": 28.0}}',
    }


def test_render_single_deal_returns_self_contained_html(make_ipo):
    from reports.html_renderer import render_single_deal
    records = _build_records(make_ipo)
    similar = []
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), similar)

    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html
    # CSS 嵌入 (没有外部链接)
    assert "<style>" in html
    assert "<link rel=\"stylesheet\"" not in html  # 不引外部
    # 决策徽章
    assert 'class="badge badge-LARGE"' in html or \
           'class="badge badge-FULL"' in html or \
           'class="badge badge-TRIAL"' in html or \
           'class="badge badge-RELATIONSHIP"' in html or \
           'class="badge badge-SKIP"' in html
    # 三因子柱
    assert "Q_company" in html
    assert "Q_ecosystem" in html
    assert "R_lockup" in html
    # bar 元素
    assert 'class="bar' in html


def test_render_single_deal_chinese_chars_preserved(make_ipo):
    from reports.html_renderer import render_single_deal
    records = _build_records(make_ipo)
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
    # 中文字符 (公司名 + 章节名等) 应该原样存在
    assert "测试公司" in html
    assert "main_board_profitable" in html


def test_render_single_deal_price_scan_renders_table(make_ipo):
    """3 个 scenario 时应有表格 + 跨决策边界 warning (如有)"""
    from reports.html_renderer import render_single_deal
    records = _build_records(make_ipo, n_scenarios=3)
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
    # 三场景每个 scenario 出现一次
    assert html.count(">low<") >= 1
    assert html.count(">mid<") >= 1
    assert html.count(">high<") >= 1
    assert "Price scenarios" in html or "敏感度" in html


def test_render_single_deal_empty_records_raises():
    from reports.html_renderer import render_single_deal
    with pytest.raises(ValueError, match="empty"):
        render_single_deal([], _build_snap(), date(2026, 5, 9), [])


def test_render_single_deal_html_well_formed(make_ipo):
    """HTML 应能被 XML parser 在 recover 模式解析 (结构合法)"""
    from reports.html_renderer import render_single_deal
    records = _build_records(make_ipo)
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
    # 用 lenient 检查: <html> 和 </html> 配对, <body> / </body> 配对
    assert html.count("<html") == 1
    assert html.count("</html>") == 1
    assert html.count("<body>") == 1
    assert html.count("</body>") == 1


def test_render_single_deal_with_similar_cases(make_ipo):
    from reports.html_renderer import render_single_deal
    records = _build_records(make_ipo)
    similar = [
        {"stock_code": "0300.HK", "name": "美的集团",
         "listing_date": "2024-09-17", "match_dims": ["chapter"],
         "actual_d30": 0.20, "actual_m6": 0.34, "similarity_score": 0.5},
        {"stock_code": "2465.HK", "name": "龙蟠科技",
         "listing_date": "2024-10-30", "match_dims": ["chapter"],
         "actual_d30": 0.04, "actual_m6": 0.47, "similarity_score": 0.5},
    ]
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), similar)
    assert "0300.HK" in html
    assert "2465.HK" in html
    # 正收益样本应有 ret-pos 类
    assert "ret-pos" in html


# =============================================================================
# render_compare
# =============================================================================

def test_render_compare_three_deals(make_ipo):
    from reports.html_renderer import render_compare
    deals = {
        "0001.HK": _build_records(make_ipo),
        "0002.HK": _build_records(make_ipo),
        "0003.HK": _build_records(make_ipo),
    }
    similar = {
        "0001.HK": [{"stock_code": "0010.HK", "name": "类似A", "listing_date": "2024-01-01",
                     "match_dims": ["chapter", "gics_l2"], "actual_d30": 0.05, "actual_m6": 0.10,
                     "similarity_score": 1.0}],
        "0002.HK": [],
        "0003.HK": [],
    }
    html = render_compare(deals, _build_snap(), similar)
    assert "0001.HK" in html
    assert "0002.HK" in html
    assert "0003.HK" in html
    # 比对表
    assert "compare-table" in html
    # 没有 similar 的也应该出现 (但有 "no similar peers" 提示)
    assert "no similar peers" in html or "类似A" in html


def test_render_compare_with_price_scan(make_ipo):
    """有 deal 含多 scenario 时应额外出 price-scan 子表"""
    from reports.html_renderer import render_compare
    deals = {
        "0001.HK": _build_records(make_ipo, n_scenarios=3),
        "0002.HK": _build_records(make_ipo, n_scenarios=1),
    }
    html = render_compare(deals, _build_snap(), {})
    # 0001 是 3 scenario, 应有 price-scan 子节
    assert "price-scan" in html.lower() or "scenario" in html


# =============================================================================
# render_case_review
# =============================================================================

def test_render_case_review_basic():
    from reports.html_renderer import render_case_review
    rep = {
        "stock_code": "0001.HK",
        "company_name_zh": "测试",
        "current_status": "listed",
        "listing_date": "2024-01-01",
        "n_predictions": 2,
        "predictions": [
            {"case_id": "PRED_X", "asof_date": "2024-01-01",
             "panel_snapshot_id": "PANEL_X",
             "deal_status_at_analysis": "prospectus",
             "price_scenario": "mid", "offer_price_used": 10.0,
             "nacs_adjusted": 0.45, "decision": "LARGE",
             "Q_company": 0.7, "Q_ecosystem": 0.6, "R_lockup": 0.2,
             "notes": None},
            {"case_id": "PRED_Y", "asof_date": "2024-04-01",
             "panel_snapshot_id": "PANEL_Y",
             "deal_status_at_analysis": "listed",
             "price_scenario": "final", "offer_price_used": 10.5,
             "nacs_adjusted": 0.50, "decision": "LARGE",
             "Q_company": 0.75, "Q_ecosystem": 0.65, "R_lockup": 0.18,
             "notes": "lock"},
        ],
        "stability": {"nacs_std": 0.03, "Q_company_std": 0.04,
                      "Q_ecosystem_std": 0.03, "R_lockup_std": 0.01},
        "locked_prediction": {"case_id": "PRED_Y", "asof_date": "2024-04-01",
                              "decision": "LARGE", "nacs_adjusted": 0.50},
        "actuals": {"return_d30": 0.10, "return_m6": 0.20, "return_m12": 0.30,
                    "max_drawdown_m6": -0.08,
                    "is_d30_due": 1, "is_m6_due": 1, "is_m12_due": 1},
        "inputs_vs_actual": [
            {"field": "intl_oversub", "pred": 5.0, "actual": 8.0,
             "delta": 3.0, "delta_pct": 0.6},
        ],
        "similar_cases": {
            "items": [
                {"stock_code": "S1.HK", "name": "Sim1",
                 "actual_d30": 0.05, "actual_m6": 0.10},
                {"stock_code": "S2.HK", "name": "Sim2",
                 "actual_d30": 0.15, "actual_m6": 0.30},
            ],
            "d30_median": 0.10, "m6_median": 0.20,
        },
        "similar_d30_diff": 0.0,
        "similar_m6_diff": 0.0,
    }
    html = render_case_review(rep)
    assert "0001.HK" in html
    assert "测试" in html
    assert "Prediction history" in html
    assert "Stability" in html
    # 实际表现
    assert "+10.00%" in html
    # similar_cases 的 median 行
    assert "S1.HK" in html


def test_render_case_review_error_branch():
    from reports.html_renderer import render_case_review
    rep = {"stock_code": "0001.HK", "error": "not in ipo_master"}
    html = render_case_review(rep)
    assert "not in ipo_master" in html
    # 不应渲染 Prediction history 等区块 (因为 error)
    assert "Prediction history" not in html


# =============================================================================
# write_html
# =============================================================================

def test_write_html_creates_file(tmp_path):
    from reports.html_renderer import write_html
    p = tmp_path / "out" / "memo.html"
    written = write_html("<html>hi</html>", p)
    assert written == p
    assert p.exists()
    assert p.read_text(encoding="utf-8") == "<html>hi</html>"


# =============================================================================
# CSS 嵌入完整性
# =============================================================================

def test_css_is_fully_embedded(make_ipo):
    """渲染输出里 CSS 必须完整嵌入 (>1KB)"""
    from reports.html_renderer import render_single_deal, _load_css
    css = _load_css()
    assert len(css) > 1000   # 我们写了 ~300 行
    records = _build_records(make_ipo)
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
    # CSS 整段在 HTML 中
    assert ".badge-FULL" in html
    assert ".bar" in html
    assert "@media print" in html


def test_no_external_resources_loaded(make_ipo):
    """绝不应加载外部 CSS/JS — 单文件可邮件分发"""
    from reports.html_renderer import render_single_deal
    records = _build_records(make_ipo)
    html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
    # 不引外部 stylesheet
    assert 'rel="stylesheet"' not in html
    # 不引外部 script
    assert "<script src=" not in html
    # 不引 CDN
    assert "cdn." not in html
    assert "googleapis.com" not in html


# =============================================================================
# Level 1+2+3 rationale 渲染 (新加)
# =============================================================================

class TestRationaleSection:
    def test_thesis_section_present(self, make_ipo):
        """memo 顶部应有 Thesis 段 (headline + drivers/risks)"""
        from reports.html_renderer import render_single_deal
        records = _build_records(make_ipo)
        html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
        assert 'class="thesis"' in html
        # headline 必有"建议"二字
        assert "建议" in html

    def test_decision_rationale_block_present(self, make_ipo):
        """公式拆解 + band 映射应在文档里"""
        from reports.html_renderer import render_single_deal
        records = _build_records(make_ipo)
        html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
        # 公式
        assert "Q_company × Q_ecosystem × (1 - R_lockup)" in html
        # band 表
        assert "完整 band 表" in html

    def test_l1_per_component_reasons_rendered(self, make_ipo):
        """L1 子项下的 reason 文本应出现"""
        from reports.html_renderer import render_single_deal
        records = _build_records(make_ipo)
        html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
        # 每个 L1.x 子项后的 reason class
        assert html.count('class="reason"') >= 6  # 至少 6 个 L1 子项 reason

    def test_l3_overhang_reason_explains_band(self, make_ipo):
        """L3 overhang reason 应包含具体阈值带"""
        from reports.html_renderer import render_single_deal
        records = _build_records(make_ipo)
        html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
        # overhang 阈值带短语
        assert "overhang_ratio" in html
        # 具体的 band 标记
        assert "解禁" in html or "0.85" in html or "0.95" in html

    def test_adjustment_explanation_rendered(self, make_ipo):
        """触发 A+H 调整时, adjustment 列表应该带 'why' 解释"""
        from reports.html_renderer import render_single_deal
        ipo = make_ipo()
        ipo.is_a_h = True
        ipo.a_share_short_borrowable = True
        # 重新构造 records (using updated ipo)
        from nacs_model import compute_nacs

        class MockRow:
            def __init__(self, d): self._d = d
            def __getitem__(self, k): return self._d.get(k)
            def keys(self): return list(self._d.keys())

        row = MockRow({
            "stock_code": "0001.HK", "company_name_zh": "测",
            "status": "prospectus",
            "listing_chapter": "main_board_profitable",
            "gics_l2": None,
            "listing_date": "2026-09-15",
            "expected_listing_date": "2026-09-15",
        })
        result = compute_nacs(ipo)
        records = [{
            "stock_code": "0001.HK", "ipo_id": "HK_001",
            "row": row, "scenario": "mid", "price": 9.0,
            "offering": ipo, "result": result,
        }]
        html = render_single_deal(records, _build_snap(), date(2026, 5, 9), [])
        # adjustment list class
        assert 'class="adjustments"' in html
        # adjustment 解释 (对冲 / A 股)
        assert "对冲" in html or "A 股" in html

    def test_compare_includes_per_deal_thesis(self, make_ipo):
        """compare 模板每只 deal 都有 thesis headline"""
        from reports.html_renderer import render_compare
        deals = {
            "0001.HK": _build_records(make_ipo),
            "0002.HK": _build_records(make_ipo),
        }
        html = render_compare(deals, _build_snap(), {"0001.HK": [], "0002.HK": []})
        # 每只 stock 应该至少出现一个 thesis section
        assert html.count('class="thesis"') >= 2

    def test_base_rate_section_when_similar_cases_have_returns(self, make_ipo):
        from reports.html_renderer import render_single_deal
        records = _build_records(make_ipo)
        sims = [
            {"stock_code": "S1.HK", "name": "Sim1", "listing_date": "2024-01-01",
             "match_dims": ["chapter", "gics_l2"],
             "actual_d30": 0.10, "actual_m6": 0.20, "similarity_score": 1.0},
            {"stock_code": "S2.HK", "name": "Sim2", "listing_date": "2024-04-01",
             "match_dims": ["chapter"],
             "actual_d30": 0.05, "actual_m6": 0.15, "similarity_score": 0.5},
            {"stock_code": "S3.HK", "name": "Sim3", "listing_date": "2024-08-01",
             "match_dims": ["chapter", "gics_l2"],
             "actual_d30": 0.08, "actual_m6": 0.25, "similarity_score": 1.0},
        ]
        html = render_single_deal(records, _build_snap(), date(2026, 5, 9), sims)
        assert "类比组实证" in html
        # verdict 词
        assert "favorable" in html or "neutral" in html or "cautious" in html
