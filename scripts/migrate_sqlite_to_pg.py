"""CLI: migrate NACS v8 SQLite assets to PostgreSQL.

This is the workhorse script of Phase 2's NACS legacy inheritance plan
(see ADR 0005 §1 for the complete table mapping). One-shot, idempotent,
required precondition for Phase 2 DONE.

Source:    data/nacs_real.db (SQLite, 14 tables, ~385 IPOs + 1,314 cornerstones)
Target:    PostgreSQL schema per PROJECT_SPEC.md §5

Tables migrated (ADR 0005 §1):
- ipo_master          → ipo_events + ipo_pricings
- ipo_returns         → ipo_postmarket
- ipo_financials      → prospectus_extractions.extraction JSONB (or
                        financial_snapshots table; Phase 1 schema decides)
- cornerstone_master  → cornerstone_investors
- cornerstone_aliases → cornerstone_investors.aliases JSONB
- ipo_cornerstone_link → cornerstone_investments
- market_environment_cache → backtest/regime_detection fixture (JSON/parquet)

NOT migrated (intentionally):
- cornerstone_performance_asof (31k rows) — recomputed in Phase 7.5 against
  new standardized benchmarks
- panel_snapshots — replaced by prediction_snapshots (immutable)

Required pre-step: back up data/nacs_real.db to
data/nacs_real.db.bak_<YYYYMMDD-HHMMSS>.

TODO (Phase 2): implement per ADR 0005 Progress checklist.
"""
