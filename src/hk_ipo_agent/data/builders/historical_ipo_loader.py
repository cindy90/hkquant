"""Load 2022-至今 HK IPO history into PostgreSQL.

Inherits from NACS v8 legacy assets per ADR 0005 §1:
- Primary source: `data/nacs_real.db` (384 IPOs already curated, 4 years of 实证).
  Migrated by `scripts/migrate_sqlite_to_pg.py` into `ipo_events` + `ipo_pricings`
  + `ipo_postmarket` + financial snapshots.
- Fallback / incremental source: iFind SDK (`data/sources/ifind_client.py`)
  for new IPOs and for backfilling fields missing in SQLite.

TODO (Phase 2): implement per PROJECT_SPEC.md §3.4 and ADR 0005 Progress checklist.
"""
