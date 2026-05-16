"""Theme system loader per ADR 0005 §5.

Copies the NACS ``themes/`` JSON resources into ``data/knowledge_base/themes/``
where Sentiment Agent (Phase 5) will read them. The legacy daily-cron updater
``themes/theme_tracker.py`` is migrated to ``scripts/update_theme_heat.py`` later.

Files handled (verbatim copy; no schema transformation needed):
- theme_definitions.json   — taxonomy
- heat_today.json          — daily heat 0-100
- premium_curve.json       — quarterly valuation premium
- ai_revenue_manual.json   — AI gilding detector input
- history.csv              — 30d trend sparkline data
- research_premium_coefficient.py — quarterly research script (copied as ref)
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ...common.logging import get_logger

log = get_logger(__name__)

THEME_FILES: tuple[str, ...] = (
    "theme_definitions.json",
    "heat_today.json",
    "premium_curve.json",
    "ai_revenue_manual.json",
    "history.csv",
)


@dataclass
class ThemeLoadReport:
    source_dir: Path
    target_dir: Path
    copied: list[str]
    missing: list[str]


class ThemeLoader:
    """Copies NACS legacy themes/ files into the new knowledge_base/themes/.

    Idempotent: overwrites existing target files.
    """

    def __init__(self, *, source_dir: Path, target_dir: Path) -> None:
        self.source_dir = source_dir
        self.target_dir = target_dir

    def load_all(self) -> ThemeLoadReport:
        self.target_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        missing: list[str] = []
        for fname in THEME_FILES:
            src = self.source_dir / fname
            if not src.exists():
                missing.append(fname)
                continue
            dst = self.target_dir / fname
            shutil.copy2(src, dst)
            copied.append(fname)
        log.info(
            "theme_loader_done",
            source=str(self.source_dir),
            target=str(self.target_dir),
            copied=copied,
            missing=missing,
        )
        return ThemeLoadReport(
            source_dir=self.source_dir,
            target_dir=self.target_dir,
            copied=copied,
            missing=missing,
        )


__all__ = ("THEME_FILES", "ThemeLoadReport", "ThemeLoader")
