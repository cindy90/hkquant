"""Knowledge-base tool — read-only access to ``data/knowledge_base/``.

Per PROJECT_SPEC.md §3.6. Provides:
- ``themes()`` — read ``themes/heat_today.json`` (ADR 0005 §5)
- ``theme_definitions()`` — read ``themes/theme_definitions.json``
- ``ai_revenue_manual()`` — read ``themes/ai_revenue_manual.json`` (AI gilding base)
- ``cornerstone_profiles()`` / ``sponsor_track_records()`` — Phase 2 builders

The themes/ JSONs were originally at repo-root ``themes/`` (NACS legacy).
**Phase 9a (ADR 0014)** archived that path to ``legacy/themes/`` after
``theme_loader.py`` copied them to ``data/knowledge_base/themes/``.
This tool reads from BOTH locations and prefers the new path; the
legacy fallback now points into ``legacy/themes/`` so dev workflows
that bypass the ETL still work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Repo root: src/hk_ipo_agent/agents/tools/kb_tool.py -> ../../../../
_REPO_ROOT: Path = Path(__file__).resolve().parents[4]
_KB_ROOT: Path = _REPO_ROOT / "data" / "knowledge_base"
_LEGACY_THEMES: Path = _REPO_ROOT / "legacy" / "themes"


def _read_json(*candidates: Path) -> dict[str, Any]:
    """Return the first existing file's parsed JSON, else empty dict."""
    for p in candidates:
        if p.exists():
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            return payload if isinstance(payload, dict) else {}
    return {}


class KBTool:
    """Inject this into ``AgentContext.kb_tool``.

    All readers are synchronous — JSON files are tiny (<200 KB) and reading
    them is not worth the async overhead. Each call re-reads to pick up
    overnight updates without restart; cache via memoization at the agent
    level if hot-path performance matters.
    """

    # ---------------------------------------------------------------- themes

    def themes_heat(self) -> dict[str, Any]:
        """Today's per-theme heat snapshot. See ``themes/heat_today.json``."""
        return _read_json(_KB_ROOT / "themes" / "heat_today.json", _LEGACY_THEMES / "heat_today.json")

    def theme_definitions(self) -> dict[str, Any]:
        """Theme taxonomy (iv_bkid + core_companies + keywords)."""
        return _read_json(
            _KB_ROOT / "themes" / "theme_definitions.json",
            _LEGACY_THEMES / "theme_definitions.json",
        )

    def ai_revenue_manual(self) -> dict[str, Any]:
        """Hand-curated AI revenue share table — used for AI gilding detection."""
        return _read_json(
            _KB_ROOT / "themes" / "ai_revenue_manual.json",
            _LEGACY_THEMES / "ai_revenue_manual.json",
        )

    def premium_curve(self) -> dict[str, Any]:
        """AI revenue % → PE premium regression curve (Phase 8 will refresh)."""
        return _read_json(
            _KB_ROOT / "themes" / "premium_curve.json",
            _LEGACY_THEMES / "premium_curve.json",
        )

    # ---------------------------------------------------------------- macro / market env

    def market_env_cache(self) -> dict[str, Any]:
        """NACS market environment cache (Phase 2 ETL output)."""
        return _read_json(_KB_ROOT / "market_env_cache.json")

    # ---------------------------------------------------------------- helpers

    def match_themes(self, *, industry_code: str, company_name: str) -> list[str]:
        """Return list of matching theme_ids based on keyword overlap.

        Conservative match: theme matches if ``industry_code`` or
        ``company_name`` contains any keyword (case-insensitive substring).
        """
        defs = self.theme_definitions().get("themes", {})
        target = f"{industry_code} {company_name}".lower()
        matches: list[str] = []
        for theme_id, payload in defs.items():
            keywords = payload.get("keywords", []) or []
            label = (payload.get("label") or "").lower()
            for kw in keywords:
                if kw.lower() in target:
                    matches.append(theme_id)
                    break
            else:
                if label and label in target:
                    matches.append(theme_id)
        return matches


__all__ = ("KBTool",)
