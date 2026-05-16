"""Regulatory regime change point detection and market regime tracking.

Inherits from NACS v8 per ADR 0005 §1 + §3:
- Initial training set: `data/nacs_real.db.market_environment_cache` (55 rows
  monthly snapshots: HSI return / volatility / southbound flow). Migrated as
  JSON or parquet fixture in Phase 2 (NOT into a PG table — this is reference
  data, not source of truth).
- Regulatory change points:
    - 2024-09-01: 18C market-cap threshold downward revision
    - 2025-08-04: new IPO pricing rules (35% clawback, Mechanism A/B)
  Backtests MUST evaluate metrics separately by regime (`backtest/runner.py`
  uses this module to slice samples).
- Market regime score: 30-day median return of recently listed HK IPOs in
  [t-120, t-30] window. Used by `agents/policy_agent.py` (ADR 0005 §2).

TODO (Phase 8): implement per PROJECT_SPEC.md §3.9 and ADR 0005 Progress checklist.
"""
