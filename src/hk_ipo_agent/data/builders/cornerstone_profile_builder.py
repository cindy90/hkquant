"""Build cornerstone investor profiles knowledge base.

Inherits from NACS v8 legacy assets per ADR 0005 §1 and §2:
- Primary seed: 1,314 cornerstone investors in `data/nacs_real.db.cornerstone_master`
  (category classification, parent_org, home_country, AUM tags), plus 1,051
  aliases in `cornerstone_aliases` (80% name coverage) and 1,604 IPO-investor
  links in `ipo_cornerstone_link`. Migrated via `scripts/migrate_sqlite_to_pg.py`.
- Derived: ultimate_holder clustering (NACS v7 "Cluster Bonus" data basis) used
  by `agents/cornerstone_signal_agent.py` to detect industry-capital syndicates
  splitting across multiple SPVs.
- NOT migrated: `cornerstone_performance_asof` (31k rows) — recomputed in Phase 7.5
  by `prediction_registry/outcome_tracker.py` against new standardized benchmarks.

TODO (Phase 2): implement per PROJECT_SPEC.md §3.4 and ADR 0005 Progress checklist.
"""
