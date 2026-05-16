"""Sentiment agent: market temperature, narrative risk.

Inherits from NACS v8 per ADR 0005 §2 + §5 — Theme Heat & AI Gilding:
- Theme Heat: daily 0-100 score per theme (AI / 半导体 / 新能源 / 等), updated
  by cron via `themes/theme_tracker.py` (legacy) → `scripts/update_theme_heat.py`
  (post Phase 2). Persisted at `themes/heat_today.json` (legacy path) and
  later `data/knowledge_base/themes/heat_today.json`.
- 30-day trend: `themes/history.csv` for sparkline narrative momentum.
- Premium curve: `themes/premium_curve.json` (quarterly update via
  `themes/research_premium_coefficient.py`) — used to detect when a theme's
  valuation multiple is over-/under-extended vs its own history.
- AI Gilding detector: if a company claims AI exposure but its AI revenue
  share is <10% (per `themes/ai_revenue_manual.json` or LLM-extracted
  revenue breakdown), apply ×0.85 narrative discount (let Synthesizer
  aggregate; do not apply multiplicatively here).
- Theme taxonomy: `themes/theme_definitions.json` (core companies + keywords
  per theme) — used for matching companies to themes.

Other dimensions (per PROJECT_SPEC.md §7.2):
- Market temperature: contemporaneous IPO pricing / aftermarket warmth.
- Narrative risk: media sentiment, contradiction with hard numbers.

TODO (Phase 5): implement per PROJECT_SPEC.md §7 and ADR 0005 Progress checklist.
"""
