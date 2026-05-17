# Phase 9c case studies

Per **ADR 0014 §9c**. Each markdown documents one already-listed HK IPO
with the data needed to reproduce a backtest run end-to-end:

- HKEX-disclosed metadata (listing_type, pricing_date, listing_date,
  issue_size_hkd, final_price, oversubscription multiples)
- Realized post-IPO returns (day1 / day22 / day126 / day252 from
  `ipo_postmarket`)
- Cornerstone disclosure count (from `cornerstone_investments`)
- V8LiteScorer projected decision_score (deterministic, no LLM)
- **Recipe** for running `FullPipelineScorer` once a real prospectus
  PDF + iFind credentials are available

## Why these 5 names

Per PROJECT_SPEC.md §4 Phase 9 deliverable + ADR 0014 §9c:

| Stock | Name | Listing | Notable |
|---|---|---|---|
| 2228.HK | 晶泰控股 (QuantumPharm) | 2024-06-13 | First spec-listed 18C alumnus — drives v0.7.5d end-to-end DAG sim |
| 2533.HK | 黑芝麻智能 | 2024-08-08 | First 18C-COMM mass-market listing (auto chip / ADAS) |
| 2432.HK | 越疆 (Dobot) | 2024-12-23 | Cobot / education — `+2.06` 126d return reproduces v8 winner pattern |
| 3750.HK | 宁德时代 (CATL H) | 2025-05-20 | First mega-cap AH dual-listing in the new ruleset window |
| 9660.HK | 地平线机器人-W | 2024-10-24 | Auto AI chip — strong post-IPO trajectory (+0.70 at 252d) |

## What's NOT in the markdowns

- **Prospectus PDF text** — gitignored per CLAUDE.md data-safety rule.
  User must drop the real PDF into `data/raw/prospectus/` before
  running FullPipelineScorer.
- **iFind credentials** — must be loaded via `IFIND__USERNAME` /
  `IFIND__PASSWORD` env vars per the `data_sources.yaml` config.
- **LLM cost** — each FullPipelineScorer run is ~$5 (Sonnet 4 +
  Opus 4.7 across 7 agents). 5 cases ≈ $25.

The markdowns therefore document the **deterministic observable
state** and the **reproducible recipe** — the user supplies the
external secrets + data to upgrade from V8Lite to Full.

## Running a case end-to-end

```bash
# 0. Ensure docker postgres is up and ETL has been run.
docker compose up -d postgres
uv run python scripts/migrate_sqlite_to_pg.py --no-backup

# 1. V8Lite-only run (no LLM, deterministic baseline):
uv run python scripts/run_backtest.py --min-date 2024-01-01 --persist

# 2. View via the API once persisted:
#    GET /api/backtest/runs                    — list runs
#    GET /api/backtest/runs/{run_id}           — sample-by-sample detail

# 3. Single-case Full-Pipeline run (user-supplied PDF + iFind):
#    See per-case "Reproducibility recipe" sections below.
```
