"""
classify_deal_to_theme — Deal → theme_id 分类器测试.

覆盖:
    - core_company 命中 → high confidence
    - keyword 命中 (1 个 / 多个) → low / medium
    - 港股代码前导零规范化 (0992 vs 00992)
    - 没匹配 → None / 'none'
    - theme_definitions=None 时 graceful 降级
    - candidates 按 score 降序
    - matched_signals 含 (signal, value, theme_id) 可审计追溯
"""
from __future__ import annotations

from typing import Dict

import pytest


@pytest.fixture
def fake_defs() -> Dict:
    """构造一个 mini 的 theme_definitions, 隔离生产数据."""
    return {
        "_schema_version": "1.0",
        "themes": {
            "ai_server": {
                "label": "AI 服务器",
                "core_companies": [
                    {"code": "00992.HK", "name": "联想集团", "role": "..."},
                    {"code": "06088.HK", "name": "FIT HON TENG", "role": "..."},
                ],
                "keywords": ["AI 服务器", "算力", "数据中心"],
            },
            "humanoid_robot": {
                "label": "人形机器人",
                "core_companies": [
                    {"code": "02382.HK", "name": "舜宇光学", "role": "..."},
                ],
                "keywords": ["人形机器人", "具身智能", "Optimus"],
            },
            "semi_localization": {
                "label": "半导体国产化",
                "core_companies": [
                    {"code": "00981.HK", "name": "中芯国际", "role": "..."},
                ],
                "keywords": ["半导体", "国产替代", "晶圆"],
            },
        },
    }


# =============================================================================
# core_company 命中
# =============================================================================

class TestCoreCompanyMatch:
    def test_high_confidence_when_code_in_core_companies(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("00992.HK", theme_definitions=fake_defs)
        assert r.theme_id == "ai_server"
        assert r.confidence == "high"
        assert any(s["signal"] == "core_company" for s in r.matched_signals)

    def test_leading_zero_normalization_4_digit(self, fake_defs):
        """0992.HK (no leading 0) 应能匹配 00992.HK"""
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("0992.HK", theme_definitions=fake_defs)
        assert r.theme_id == "ai_server"
        assert r.confidence == "high"

    def test_leading_zero_normalization_5_digit(self, fake_defs):
        """00992.HK 应能匹配 (反向)"""
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("00992.HK", theme_definitions=fake_defs)
        assert r.theme_id == "ai_server"

    def test_case_insensitive(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("00992.hk", theme_definitions=fake_defs)
        assert r.theme_id == "ai_server"


# =============================================================================
# Keyword 命中
# =============================================================================

class TestKeywordMatch:
    def test_single_keyword_hit_low_confidence(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "9999.HK", company_name="测试半导体公司",
            theme_definitions=fake_defs)
        assert r.theme_id == "semi_localization"
        assert r.confidence == "low"

    def test_multiple_keywords_medium_confidence(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "9999.HK",
            ipo_concept_names=["人形机器人概念", "具身智能"],
            theme_definitions=fake_defs,
        )
        assert r.theme_id == "humanoid_robot"
        assert r.confidence == "medium"

    def test_keyword_in_gics_l2(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "9999.HK", gics_l2="资讯科技业-AI 服务器子板块",
            theme_definitions=fake_defs,
        )
        assert r.theme_id == "ai_server"


# =============================================================================
# Confidence 升级路径
# =============================================================================

class TestConfidenceEscalation:
    def test_core_company_outweighs_keywords_in_other_themes(self, fake_defs):
        """同时命中 ai_server core_company + humanoid_robot keyword → 选 core (score 10 > 3)"""
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "00992.HK", ipo_concept_names=["人形机器人"],
            theme_definitions=fake_defs,
        )
        assert r.theme_id == "ai_server"
        assert r.confidence == "high"
        # candidates 应同时列出 humanoid_robot 作为备选
        cand_ids = [c["theme_id"] for c in r.candidates]
        assert "humanoid_robot" in cand_ids


# =============================================================================
# 没匹配
# =============================================================================

class TestNoMatch:
    def test_none_when_haystack_empty(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("9999.HK", theme_definitions=fake_defs)
        assert r.theme_id is None
        assert r.confidence == "none"

    def test_none_when_no_keyword_or_company_match(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "9999.HK", gics_l2="医疗保健",
            ipo_concept_names=["糖尿病药物", "次新股"],
            company_name="测试医药",
            theme_definitions=fake_defs,
        )
        assert r.theme_id is None

    def test_graceful_degrade_when_definitions_none(self):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("9999.HK", theme_definitions=None)
        assert r.theme_id is None
        assert r.confidence == "none"
        assert "未加载" in r.match_reason


# =============================================================================
# Audit trail
# =============================================================================

class TestAuditTrail:
    def test_matched_signals_records_each_hit(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "9999.HK",
            ipo_concept_names=["人形机器人", "具身智能", "Optimus"],
            theme_definitions=fake_defs,
        )
        assert len(r.matched_signals) == 3
        for s in r.matched_signals:
            assert s["signal"] == "keyword"
            assert s["theme_id"] == "humanoid_robot"

    def test_candidates_sorted_by_score_desc(self, fake_defs):
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme(
            "9999.HK",
            ipo_concept_names=["AI 服务器", "算力", "人形机器人"],
            theme_definitions=fake_defs,
        )
        # ai_server 命中 2 keywords (6), humanoid_robot 命中 1 (3)
        assert r.candidates[0]["theme_id"] == "ai_server"
        assert r.candidates[0]["score"] == 6
        assert r.candidates[1]["theme_id"] == "humanoid_robot"
        assert r.candidates[1]["score"] == 3

    def test_to_dict_jsonable(self, fake_defs):
        """ClassificationResult 可序列化进 nacs_predictions"""
        import json
        from reports.themes_data import classify_deal_to_theme
        r = classify_deal_to_theme("00992.HK", theme_definitions=fake_defs)
        s = json.dumps(r.to_dict(), ensure_ascii=False)
        assert "ai_server" in s
        assert "high" in s


# =============================================================================
# 真实生产数据 sanity
# =============================================================================

class TestRealProduction:
    def test_real_definitions_classifies_lenovo(self):
        """0992.HK (联想) 应该 high confidence 命中 ai_server"""
        from reports.themes_data import (
            classify_deal_to_theme, load_theme_definitions,
        )
        defs, _ = load_theme_definitions()
        r = classify_deal_to_theme("0992.HK", theme_definitions=defs)
        assert r.theme_id == "ai_server"
        assert r.confidence == "high"

    def test_real_definitions_classifies_tencent(self):
        from reports.themes_data import (
            classify_deal_to_theme, load_theme_definitions,
        )
        defs, _ = load_theme_definitions()
        r = classify_deal_to_theme("0700.HK", theme_definitions=defs)
        assert r.theme_id == "llm"
        assert r.confidence == "high"
