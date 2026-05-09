"""
themes_data 加载器测试.

覆盖:
    - 5 个 loader 各自的 happy path / missing / corrupt 路径
    - provenance 元数据 (path, mtime, schema_version, asof, is_stale, notes)
    - heat_today 时鲜阈值 (HEAT_STALE_DAYS=3)
    - premium_curve r_squared/n_samples_used 偏低时 notes 警告
    - history.csv 排序 + last_n_days 截断
    - ai_revenue_manual needs_review=true 样本被排除
    - load_all 一次拿全 (非异常)
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest


# =============================================================================
# Helpers
# =============================================================================

def _make_themes_dir(tmp_path: Path, **files) -> Path:
    """tmp_path/themes/ 写入指定文件."""
    d = tmp_path / "themes"
    d.mkdir()
    for fname, content in files.items():
        p = d / fname
        if isinstance(content, (dict, list)):
            p.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        else:
            p.write_text(content, encoding="utf-8")
    return d


# =============================================================================
# load_heat_today
# =============================================================================

class TestLoadHeatToday:
    def test_missing_returns_none(self, tmp_path):
        from reports.themes_data import load_heat_today
        d = tmp_path / "themes"
        d.mkdir()
        data, prov = load_heat_today(d)
        assert data is None
        assert prov.status == "missing"
        assert prov.path.endswith("heat_today.json")

    def test_corrupt_json_returns_none(self, tmp_path):
        from reports.themes_data import load_heat_today
        d = tmp_path / "themes"
        d.mkdir()
        (d / "heat_today.json").write_text("{not json", encoding="utf-8")
        data, prov = load_heat_today(d)
        assert data is None
        assert prov.status == "corrupt"
        assert any("JSONDecodeError" in n for n in prov.notes)

    def test_happy_path(self, tmp_path):
        from reports.themes_data import load_heat_today
        d = _make_themes_dir(tmp_path, **{"heat_today.json": {
            "as_of": "2026-05-08",
            "themes": {"ai_server": {"heat_score": 72, "label": "AI 服务器"}},
        }})
        data, prov = load_heat_today(d, today=date(2026, 5, 9))
        assert data["as_of"] == "2026-05-08"
        assert data["themes"]["ai_server"]["heat_score"] == 72
        assert prov.status == "ok"
        assert prov.asof == "2026-05-08"
        assert prov.is_stale is False
        assert prov.mtime_iso is not None

    def test_stale_when_asof_too_old(self, tmp_path):
        """as_of 距 today > 3 天 → is_stale=True"""
        from reports.themes_data import load_heat_today
        d = _make_themes_dir(tmp_path, **{"heat_today.json": {
            "as_of": "2026-05-01", "themes": {},
        }})
        data, prov = load_heat_today(d, today=date(2026, 5, 9))  # 8 天前
        assert prov.is_stale is True
        assert any("已 8 天" in n for n in prov.notes)

    def test_real_themes_dir_loads(self, project_root):
        """跑一遍真 themes/heat_today.json"""
        from reports.themes_data import load_heat_today
        data, prov = load_heat_today()  # default
        assert prov.status == "ok"
        assert isinstance(data, dict)
        assert "themes" in data


# =============================================================================
# load_premium_curve
# =============================================================================

class TestLoadPremiumCurve:
    def test_missing(self, tmp_path):
        from reports.themes_data import load_premium_curve
        d = tmp_path / "themes"; d.mkdir()
        data, prov = load_premium_curve(d)
        assert data is None and prov.status == "missing"

    def test_low_r_squared_warns(self, tmp_path):
        from reports.themes_data import load_premium_curve
        d = _make_themes_dir(tmp_path, **{"premium_curve.json": {
            "fitted_at": "2026-05-08T19:22:21",
            "as_of_data": "2026-05-08",
            "n_samples_total": 36, "n_samples_used": 31,
            "model": "log_linear", "params": {"a": 5.17, "b": 0.5, "c": -0.23},
            "r_squared": 0.25,            # 低 R²
            "lookup_table": [],
        }})
        data, prov = load_premium_curve(d, today=date(2026, 5, 9))
        assert any("r_squared=0.25" in n for n in prov.notes)

    def test_low_sample_count_warns(self, tmp_path):
        from reports.themes_data import load_premium_curve
        d = _make_themes_dir(tmp_path, **{"premium_curve.json": {
            "fitted_at": "2026-05-08T19:22:21",
            "as_of_data": "2026-05-08",
            "n_samples_used": 12,           # 少
            "r_squared": 0.45,
        }})
        data, prov = load_premium_curve(d, today=date(2026, 5, 9))
        assert any("n_samples_used=12<20" in n for n in prov.notes)

    def test_stale_when_old_fit(self, tmp_path):
        from reports.themes_data import load_premium_curve
        d = _make_themes_dir(tmp_path, **{"premium_curve.json": {
            "fitted_at": "2026-01-08",
            "as_of_data": "2026-01-08",     # 4 个月前
            "n_samples_used": 30, "r_squared": 0.50,
        }})
        data, prov = load_premium_curve(d, today=date(2026, 5, 9))
        assert prov.is_stale is True
        assert any("research_premium_coefficient" in n for n in prov.notes)


# =============================================================================
# load_theme_definitions
# =============================================================================

class TestLoadThemeDefinitions:
    def test_real_loads_8_themes(self):
        """生产 themes/theme_definitions.json 应有 8 个主题"""
        from reports.themes_data import load_theme_definitions
        data, prov = load_theme_definitions()
        assert prov.status == "ok"
        assert prov.schema_version == "1.0"
        themes = data.get("themes", {})
        assert len(themes) == 8
        for tid, td in themes.items():
            assert "label" in td
            assert "core_companies" in td
            assert "keywords" in td

    def test_corrupt_returns_none(self, tmp_path):
        from reports.themes_data import load_theme_definitions
        d = tmp_path / "themes"; d.mkdir()
        (d / "theme_definitions.json").write_text("garbage", encoding="utf-8")
        data, prov = load_theme_definitions(d)
        assert data is None
        assert prov.status == "corrupt"


# =============================================================================
# load_ai_revenue_manual
# =============================================================================

class TestLoadAIRevenueManual:
    def test_filters_needs_review(self, tmp_path):
        """key 经过 _canon 规范化 (去前导 0) — 0001.HK → 1.HK"""
        from reports.themes_data import load_ai_revenue_manual
        d = _make_themes_dir(tmp_path, **{"ai_revenue_manual.json": {
            "_schema_version": "1.0",
            "samples": [
                {"code": "0001.HK", "ai_revenue_pct": 0.10, "needs_review": False},
                {"code": "0002.HK", "ai_revenue_pct": 0.30, "needs_review": True},
                {"code": "0003.HK", "ai_revenue_pct": 0.50, "needs_review": False},
            ],
        }})
        data, prov = load_ai_revenue_manual(d)
        # 4 位前导 0 被去掉, 跟 classify_deal_to_theme 同源
        assert data == {"1.HK": 0.10, "3.HK": 0.50}
        # provenance notes 应记录 1 个被跳过
        assert any("skipped 1" in n for n in prov.notes)

    def test_skips_missing_code_or_pct(self, tmp_path):
        from reports.themes_data import load_ai_revenue_manual
        d = _make_themes_dir(tmp_path, **{"ai_revenue_manual.json": {
            "samples": [
                {"code": "0001.HK", "ai_revenue_pct": 0.10},
                {"name": "no code", "ai_revenue_pct": 0.20},
                {"code": "0003.HK", "ai_revenue_pct": None},
                {"code": "0004.HK", "ai_revenue_pct": "not a number"},
            ],
        }})
        data, prov = load_ai_revenue_manual(d)
        assert data == {"1.HK": 0.10}

    def test_real_loads_36_samples(self):
        """生产 ai_revenue_manual 有 42 行, 排除 6 needs_review 后 = 36"""
        from reports.themes_data import load_ai_revenue_manual
        data, prov = load_ai_revenue_manual()
        assert prov.status == "ok"
        assert len(data) == 36

    def test_real_keys_normalized_no_leading_zero(self):
        """生产数据 key 应已被 canonical 化 (4 位 vs 5 位统一)"""
        from reports.themes_data import load_ai_revenue_manual
        data, _ = load_ai_revenue_manual()
        # 所有 key 都不应以 '0' 开头 (除非整体是 '0' 即 0.HK 这种不存在的)
        for k in data:
            num = k.split(".")[0]
            assert not (len(num) > 1 and num.startswith("0")), \
                f"key {k!r} has leading zero (canonicalization broke)"
        # 顺手验 02533.HK 已规范为 2533.HK
        assert "2533.HK" in data
        assert "02533.HK" not in data


# =============================================================================
# load_history
# =============================================================================

class TestLoadHistory:
    def test_empty_history(self, tmp_path):
        from reports.themes_data import load_history
        d = _make_themes_dir(tmp_path, **{"history.csv": "date,llm,ai_server\n"})
        data, prov = load_history(d)
        assert data == {}
        assert prov.status == "ok"
        assert any("空" in n for n in prov.notes)

    def test_sorts_and_truncates(self, tmp_path):
        from reports.themes_data import load_history
        # 5 行, 取最近 3
        csv_content = (
            "date,llm,ai_server\n"
            "2026-05-05,50,60\n"
            "2026-05-03,40,55\n"          # 故意乱序
            "2026-05-08,80,72\n"
            "2026-05-04,45,58\n"
            "2026-05-06,55,62\n"
        )
        d = _make_themes_dir(tmp_path, **{"history.csv": csv_content})
        data, prov = load_history(d, last_n_days=3)
        assert "llm" in data and "ai_server" in data
        # 应取 2026-05-06 / 05-08 / (其它一个最新的 3 天)
        # 排序后取末 3 → [05-06, 05-08, ...] 实际应 [05-06, 05-08, 05-05]→sorted是 05-05 < 05-06 < 05-08 取末 3 = 05-05/05-06/05-08
        # 但还有 05-04 / 05-03... 最大 3 天是 05-06, 05-08, 05-05
        llm_dates = [d for d, _ in data["llm"]]
        assert len(llm_dates) == 3
        assert llm_dates == sorted(llm_dates)  # 升序
        # 最后一项一定是最新的 (05-08)
        assert llm_dates[-1] == "2026-05-08"

    def test_real_loads(self):
        from reports.themes_data import load_history
        data, prov = load_history()
        assert prov.status == "ok"
        # 历史还少 (1 行), 但应能加载
        assert isinstance(data, dict)


# =============================================================================
# load_all
# =============================================================================

def test_load_all_returns_5_keys():
    from reports.themes_data import load_all
    bundle = load_all()
    assert set(bundle.keys()) == {
        "heat_today", "premium_curve", "theme_definitions",
        "ai_revenue_manual", "history",
    }
    # 每个都是 (data, provenance) 二元组
    for name, (data, prov) in bundle.items():
        assert prov.path.endswith(".json") or prov.path.endswith(".csv")


def test_load_all_no_exception_on_missing(tmp_path):
    """所有文件都缺也不应抛"""
    from reports.themes_data import load_all
    d = tmp_path / "themes"
    d.mkdir()
    bundle = load_all(themes_dir=d)
    for name, (data, prov) in bundle.items():
        assert prov.status == "missing"
        assert data is None


# =============================================================================
# Provenance to_dict (用于落进 nacs_predictions)
# =============================================================================

def test_provenance_to_dict_jsonable():
    """Provenance.to_dict() 应能直接 json.dumps"""
    import json as _json
    from reports.themes_data import Provenance
    p = Provenance(path="themes/heat_today.json", status="ok",
                   mtime_iso="2026-05-09T20:00:00", asof="2026-05-08",
                   is_stale=False)
    s = _json.dumps(p.to_dict(), ensure_ascii=False)
    assert "themes/heat_today.json" in s
    assert "ok" in s
