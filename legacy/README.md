# NACS v8 Legacy Archive

Archived in **Phase 9a** per [ADR 0005 §Progress](../docs/decisions/0005-nacs-legacy-asset-migration.md)
and [ADR 0014 §9a](../docs/decisions/0014-phase9-scope-and-substages.md).

**These files are kept on disk for traceability only — they are NOT
part of the active build.** No new code should import from them; lint
and type-check are scoped to `src/hk_ipo_agent/` + `tests/{unit,integration,e2e}/`
+ `scripts/` per `pyproject.toml`.

## Why archived rather than deleted

1. **Reproducibility**: regenerating the migrated PG state from
   `data/nacs_real.db` requires the SQLite file (see
   `scripts/migrate_sqlite_to_pg.py`).
2. **Audit trail**: 4 years of empirical NACS v8 work is the basis
   for the calibration baselines (`data/fixtures/nacs_v8_baselines.json`,
   `data/fixtures/market_environment_cache.json`) and the v8 ADR
   inheritance map (ADR 0005). Walking those back requires looking at
   the actual NACS source.
3. **Spec compliance**: ADR 0005's "data assets to inherit" map calls
   out specific files; archiving rather than deleting keeps the map
   useful.

## Contents

| Path | Origin | Replaced by |
|---|---|---|
| `legacy/themes/` (10 files) | NACS theme system | `data/knowledge_base/themes/` (Phase 2 ETL) + `agents/sentiment_agent.py` + `agents/tools/kb_tool.py` |
| `legacy/data/nacs_real.db` + 4 `.bak_*` | NACS v8 SQLite (14 tables) | PostgreSQL schema per PROJECT_SPEC.md §5 (Phase 2 ETL) |
| `legacy/scripts/build_perf_cache.py` | NACS performance cache builder | Replaced by walk-forward `backtest/runner.py` + `prediction_outcomes` (Phase 8) |
| `legacy/scripts/check_health.py` | NACS pipeline health probe | `api/routers/health.py` (Phase 7) |
| `legacy/scripts/run_v7_backtest.py` | NACS v7 walk-forward harness | `backtest/runner.py` + `scripts/run_backtest.py` (Phase 8) |
| `legacy/scripts/nacs_checklist_tool.html` | NACS pre-IPO checklist UI | Replaced by spec UI (`PROJECT_SPEC_UI.md`, separate repo) |
| `legacy/configs/nacs_v8.yaml` | NACS v8 model weights | `config/*.yaml` (new spec config tree) |
| `legacy/src/config.py` | NACS hard-coded config loader | `common/settings.py` + `config/settings.yaml` |
| `legacy/src/nacs_model.py` | NACS v8 scoring model | Multi-agent LLM system (`agents/` + `valuation/` + `orchestrator/`) |
| `legacy/src/data/` (dao.py / schema.py) | NACS SQLite DAO | `data/repositories/` + SQLAlchemy ORM (Phase 1 / 2) |
| `legacy/src/data_sources/` (akshare / ifind) | NACS data ingestion | `data/sources/` + builder pattern (Phase 2) |

## Cleanup policy

- **Phase 10+**: Once the learning loop has accumulated enough new
  outcomes that NACS v8 baselines stop being the canonical reference,
  this directory becomes a candidate for `git filter-repo` permanent
  removal.
- **Until then**: contents stay read-only. `pyproject.toml` already
  excludes them from ruff / mypy / pytest.

## Re-running NACS v8 outputs

If you ever need to regenerate the NACS v8 backtest archives:

```bash
# Reproduce the SQLite → PG ETL (Phase 2 + 8d):
uv run python scripts/migrate_sqlite_to_pg.py
# Then re-run the walk-forward harness (Phase 8c/d):
uv run python scripts/run_backtest.py --persist
```

The SQLite path inside `migrate_sqlite_to_pg.py` is now hard-coded to
`data/nacs_real.db`. If you've fully archived (moved) the file,
either symlink `data/nacs_real.db -> legacy/data/nacs_real.db` or
adjust the script's `NACS_DB` constant.
