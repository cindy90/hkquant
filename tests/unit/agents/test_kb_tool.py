"""Unit tests for ``agents.tools.kb_tool.KBTool``.

Tests cover:
- ``_read_json`` multi-candidate path resolution
- ``themes_heat`` / ``theme_definitions`` / ``ai_revenue_manual`` file lookup
- ``match_themes`` keyword + label matching logic
- Graceful fallback on missing / malformed / non-dict files
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from hk_ipo_agent.agents.tools.kb_tool import KBTool, _read_json


class TestReadJson:
    """Low-level ``_read_json`` helper."""

    def test_reads_first_existing_candidate(self, tmp_path: Path) -> None:
        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        p1.write_text(json.dumps({"from": "a"}), encoding="utf-8")
        p2.write_text(json.dumps({"from": "b"}), encoding="utf-8")
        assert _read_json(p1, p2) == {"from": "a"}

    def test_falls_through_to_second_candidate(self, tmp_path: Path) -> None:
        p1 = tmp_path / "missing.json"
        p2 = tmp_path / "present.json"
        p2.write_text(json.dumps({"ok": True}), encoding="utf-8")
        assert _read_json(p1, p2) == {"ok": True}

    def test_returns_empty_dict_when_all_missing(self, tmp_path: Path) -> None:
        assert _read_json(tmp_path / "x.json", tmp_path / "y.json") == {}

    def test_skips_malformed_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        good = tmp_path / "good.json"
        bad.write_text("{invalid json", encoding="utf-8")
        good.write_text(json.dumps({"valid": 1}), encoding="utf-8")
        assert _read_json(bad, good) == {"valid": 1}

    def test_returns_empty_dict_for_non_dict_payload(self, tmp_path: Path) -> None:
        arr = tmp_path / "array.json"
        arr.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert _read_json(arr) == {}


class TestKBToolMatchThemes:
    """``KBTool.match_themes`` keyword/label matching."""

    def _tool_with_definitions(self, defs: dict) -> KBTool:
        tool = KBTool()
        # Patch theme_definitions to return controlled data.
        tool.theme_definitions = lambda: {"themes": defs}  # type: ignore[assignment]
        return tool

    def test_matches_by_keyword(self) -> None:
        tool = self._tool_with_definitions({
            "ai_infra": {"keywords": ["AI", "人工智能"], "label": "AI基础设施"},
            "ev": {"keywords": ["电动车", "新能源"], "label": "新能源汽车"},
        })
        matches = tool.match_themes(industry_code="AI", company_name="某AI公司")
        assert "ai_infra" in matches

    def test_matches_by_label_fallback(self) -> None:
        tool = self._tool_with_definitions({
            "robotics": {"keywords": [], "label": "机器人"},
        })
        matches = tool.match_themes(industry_code="other", company_name="某机器人公司")
        assert "robotics" in matches

    def test_no_match_returns_empty(self) -> None:
        tool = self._tool_with_definitions({
            "ai_infra": {"keywords": ["AI"], "label": "AI基础设施"},
        })
        matches = tool.match_themes(industry_code="pharma", company_name="制药公司")
        assert matches == []

    def test_case_insensitive_keyword_match(self) -> None:
        tool = self._tool_with_definitions({
            "saas": {"keywords": ["SaaS", "cloud"], "label": "云服务"},
        })
        matches = tool.match_themes(industry_code="CLOUD_COMPUTING", company_name="X Corp")
        assert "saas" in matches

    def test_empty_definitions_returns_empty(self) -> None:
        tool = self._tool_with_definitions({})
        assert tool.match_themes(industry_code="any", company_name="any") == []


class TestKBToolIntegration:
    """Integration-style tests with real files on disk."""

    def test_themes_heat_reads_kb_path(self, tmp_path: Path) -> None:
        kb_themes = tmp_path / "data" / "knowledge_base" / "themes"
        kb_themes.mkdir(parents=True)
        (kb_themes / "heat_today.json").write_text(
            json.dumps({"themes": {"ai": {"heat": 80}}}), encoding="utf-8"
        )
        with patch("hk_ipo_agent.agents.tools.kb_tool._KB_ROOT", tmp_path / "data" / "knowledge_base"):
            tool = KBTool()
            result = tool.themes_heat()
        assert result == {"themes": {"ai": {"heat": 80}}}

    def test_themes_heat_falls_back_to_legacy(self, tmp_path: Path) -> None:
        legacy = tmp_path / "themes"
        legacy.mkdir()
        (legacy / "heat_today.json").write_text(
            json.dumps({"themes": {"ev": {"heat": 60}}}), encoding="utf-8"
        )
        with (
            patch("hk_ipo_agent.agents.tools.kb_tool._KB_ROOT", tmp_path / "data" / "knowledge_base"),
            patch("hk_ipo_agent.agents.tools.kb_tool._LEGACY_THEMES", legacy),
        ):
            tool = KBTool()
            result = tool.themes_heat()
        assert result == {"themes": {"ev": {"heat": 60}}}
