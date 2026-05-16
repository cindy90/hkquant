"""Policy agent: regulatory regime fit, policy tailwind.

Inherits from NACS v8 per ADR 0005 §2 — Regime Gate:
- NACS v7 Regime Gate empirically validated: regime_score < 0 → SKIP filter
  produced regime≥0 subsample 60d IC = +0.247, t-stat = +2.41 (n=91).
- This agent MUST output `regime_score: float` as part of its `scores` dict so
  the valuation ensemble (see `valuation/ensemble.py`) can apply the
  regime<0 → SKIP truncation as a post-adjustment.
- Regime score computation: median 30-day return of HK IPOs listed in
  [pricing_date - 120d, pricing_date - 30d]. Data source: PostgreSQL
  `ipo_postmarket` table (after Phase 2 ETL) or `data/sources/ifind_client.py`.

Other policy dimensions (per PROJECT_SPEC.md §3.6 / §7.2):
- Regulatory regime fit: pre / post 2025-08-04 IPO rules; 18C pre / post
  2024-09-01 threshold change. Source: `config/regulations/*.yaml`.
- Policy tailwind: industry-specific subsidy / strategic positioning signals.

TODO (Phase 5): implement per PROJECT_SPEC.md §7 and ADR 0005 Progress checklist.
"""
