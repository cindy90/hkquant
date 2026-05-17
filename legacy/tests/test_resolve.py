"""
模糊匹配 (resolve_cornerstone_id + normalize_cs_name) 回归测试

样本来源: iFinD 实战中常见的基石名变体
"""
from __future__ import annotations

import pytest


# =============================================================================
# normalize_cs_name 单元测试
# =============================================================================

class TestNormalize:
    @pytest.mark.parametrize("inp,exp", [
        ("GIC Private Limited", "gic"),
        ("BlackRock Inc.", "blackrock"),
        ("JPMorgan Asset Management", "jpmorgan"),
        ("蓝思科技(香港)有限公司", "蓝思科技"),
        ("中国人寿保险股份有限公司", "中国人寿保险"),
        ("远信(珠海)私募基金管理有限公司", "远信"),
        ("Test SPV Ltd", "test"),
        ("  EXTRA   SPACES  ", "extra spaces"),
    ])
    def test_normalize_strips_suffix_and_brackets(self, inp, exp):
        from data.dao import normalize_cs_name
        assert normalize_cs_name(inp) == exp

    def test_normalize_empty(self):
        from data.dao import normalize_cs_name
        assert normalize_cs_name("") == ""
        assert normalize_cs_name("   ") == ""


# =============================================================================
# resolve_cornerstone_id 多策略
# =============================================================================

@pytest.fixture
def resolve_db(empty_db):
    from data.dao import db_connect, upsert_cornerstone, add_alias
    from nacs_model import CornerstoneType

    with db_connect(str(empty_db)) as conn:
        upsert_cornerstone(conn,
            cornerstone_id="CS_GIC", canonical_name="GIC Private Limited",
            cornerstone_type=CornerstoneType.SOVEREIGN_PENSION)
        add_alias(conn, cornerstone_id="CS_GIC",
                  alias_text="GIC Private Limited", alias_type="legal_name")

        upsert_cornerstone(conn,
            cornerstone_id="CS_BlackRock", canonical_name="BlackRock Inc",
            cornerstone_type=CornerstoneType.GLOBAL_LONG_ONLY)
        add_alias(conn, cornerstone_id="CS_BlackRock",
                  alias_text="BlackRock Inc", alias_type="legal_name")

        upsert_cornerstone(conn,
            cornerstone_id="CS_LansiHK",
            canonical_name="蓝思科技(香港)有限公司",
            cornerstone_type=CornerstoneType.STRATEGIC_INDUSTRIAL)
        add_alias(conn, cornerstone_id="CS_LansiHK",
                  alias_text="蓝思科技(香港)有限公司", alias_type="prospectus")

        upsert_cornerstone(conn,
            cornerstone_id="CS_YuanXin",
            canonical_name="远信(珠海)私募基金管理有限公司",
            cornerstone_type=CornerstoneType.PE_VC_CONTINUATION)
        add_alias(conn, cornerstone_id="CS_YuanXin",
                  alias_text="远信(珠海)私募基金管理有限公司",
                  alias_type="prospectus")
    return empty_db


def _resolve(db, name):
    """便捷包装"""
    import sqlite3
    from data.dao import resolve_cornerstone_id
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        return resolve_cornerstone_id(conn, name)


class TestResolveStrategies:
    def test_strategy1_exact(self, resolve_db):
        r = _resolve(resolve_db, "GIC Private Limited")
        assert r is not None
        assert r[0] == "CS_GIC"
        assert r[1] == 1.0

    def test_strategy1_case_insensitive(self, resolve_db):
        r = _resolve(resolve_db, "gic private limited")
        assert r is not None and r[0] == "CS_GIC"

    def test_strategy2_normalized_exact_strips_suffix(self, resolve_db):
        """招股书写 'BlackRock' 应能匹配 'BlackRock Inc'"""
        r = _resolve(resolve_db, "BlackRock")
        assert r is not None
        assert r[0] == "CS_BlackRock"
        assert r[1] >= 0.7  # 归一化精确 (0.95) 或 substring (0.7) 都可

    def test_strategy2_normalized_chinese(self, resolve_db):
        """蓝思科技 (无后缀) → 命中 蓝思科技(香港)有限公司"""
        r = _resolve(resolve_db, "蓝思科技")
        assert r is not None and r[0] == "CS_LansiHK"

    def test_strategy3_substring(self, resolve_db):
        """raw 是 alias 的子串"""
        r = _resolve(resolve_db, "GIC")
        assert r is not None and r[0] == "CS_GIC"

    def test_strategy4_token_jaccard_yuanxin(self, resolve_db):
        """字段顺序略变 / 多余字: 远信投资 → 远信"""
        r = _resolve(resolve_db, "远信投资")
        assert r is not None and r[0] == "CS_YuanXin"

    def test_strategy5_typo_tolerance(self, resolve_db):
        """轻微拼写差异 (typo): BlackRok → BlackRock"""
        r = _resolve(resolve_db, "BlackRok Inc")
        assert r is not None and r[0] == "CS_BlackRock"

    def test_no_match_returns_none(self, resolve_db):
        r = _resolve(resolve_db, "Some Completely Unrelated Random Fund")
        # 注意: 短词可能与某 alias 偶然有低 Jaccard, 默认 min_confidence=0.5 兜底
        if r is not None:
            assert r[1] < 0.6, f"低相关名不应高 confidence, 实际 {r}"

    def test_min_confidence_filters(self, resolve_db):
        """提高阈值能过滤模糊匹配"""
        import sqlite3
        from data.dao import resolve_cornerstone_id
        with sqlite3.connect(str(resolve_db)) as conn:
            conn.row_factory = sqlite3.Row
            # typo 在 0.95 阈值下应被滤掉
            r = resolve_cornerstone_id(conn, "BlackRok Inc", min_confidence=0.95)
        assert r is None

    def test_empty_input(self, resolve_db):
        assert _resolve(resolve_db, "") is None
        assert _resolve(resolve_db, "   ") is None
