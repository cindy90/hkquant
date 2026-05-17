# 2228.HK — 晶泰控股 (QuantumPharm) Case Study

> Phase 9c case study per ADR 0014. Golden e2e regression case —
> exercised by `tests/e2e/test_quantumpharm_case.py`.

## HKEX-disclosed metadata

| Field | Value |
|---|---|
| Stock code | `2228.HK` |
| Company (zh) | 晶泰控股 |
| Listing chapter | **MB-OTHER** (originally filed under 18C; reclassified in ETL) |
| Industry | 医疗保健业 / 药品及生物科技 / 生物科技 |
| Pricing date | **2024-06-04** |
| Listing date | **2024-06-13** |
| Issue size (HKD) | 1,035,772,320 |
| Final price (HKD) | 5.28 |
| International oversub | 2.13x |
| Retail oversub | 103.35x |

## Realized post-IPO returns (from `ipo_postmarket`)

| Horizon | Return |
|---|---:|
| Day 1 | **-8.62%** |
| Day 5 | (n/a in NACS scalars) |
| Day 22 | +0.17% |
| Day 126 | **+15.86%** |
| Day 252 | +0.19% |

**Read**: A typical first-day disappointment (–9%) that quietly
recovered to +16% by D+126 then settled flat by year. The 6-month
peak is the right exit, consistent with NACS v8 regime-pass empirics.

## Cornerstone disclosure

- Disclosed cornerstones at PHIP: **8** investors
- Cluster (≥2 SPVs sharing ultimate_holder): yes (signal valuable per
  ADR 0005 §2 — `Cluster Bonus`)

## V8LiteScorer projection (deterministic, no LLM)

Reproducible via:

```bash
uv run python -c "
import asyncio, uuid
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.backtest.runner import (
    V8LiteScorer, run_walk_forward, load_backtest_inputs_from_pg,
)
async def main():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        inputs = await load_backtest_inputs_from_pg(sf, min_pricing_date=date(2024, 6, 1))
        case = [i for i in inputs if i.stock_code == '2228.HK']
        run = await run_walk_forward(case, scorer=V8LiteScorer(), session_factory=sf)
        s = run.samples[0]
        print(f'decision_score={s.decision_score:+.3f} regime={s.regime_score:+.3f} pass={s.regime_pass}')
    finally:
        await engine.dispose()
asyncio.run(main())
"
```

## Reproducibility recipe — FullPipelineScorer

To upgrade from V8Lite to full multi-agent pipeline:

1. **Drop the real PHIP / AP1 prospectus PDF** at
   `data/raw/prospectus/2228_HK_phip.pdf` (gitignored per
   CLAUDE.md data-safety rule).

2. **Set iFind credentials** in `.env`:
   ```
   IFIND__USERNAME=...
   IFIND__PASSWORD=...
   ANTHROPIC_API_KEY=sk-ant-...
   ```

3. **Run the Phase 3 prospectus extractor** to populate
   `prospectus_docs` (and the Phase 3 extraction blob):
   ```bash
   uv run python scripts/extract_prospectus.py --ipo 2228.HK
   ```

4. **Run FullPipelineScorer in a one-IPO walk-forward**:
   ```python
   from hk_ipo_agent.backtest.full_scorer import (
       FullPipelineScorer, make_fixture_extraction_fetcher,
   )
   from hk_ipo_agent.common.llm_client import LLMClient
   # ... build LLMClient + ExtractionFetcher backed by prospectus_docs query,
   #     then plug into run_walk_forward() in place of V8LiteScorer().
   ```

   Expected wall clock: ~3-5 minutes (Phase 5 / 6 / 7 agents fan out).
   LLM cost: ~$5. Stays within PROJECT_SPEC.md §13 30-minute SLO.

5. **Compare predictions to the realized returns above** in the
   resulting `prediction_outcomes` rows.

## Open questions for live run

- Did the synthesizer recommend SKIP given the day-1 break? (V8Lite
  scored regime_pass=true on 2024-06-03 cache; full pipeline may
  differ once Sentiment Agent sees the prospectus theme overlap.)
- Did the Cluster Bonus fire correctly? (8 cornerstones → expected
  +0.20 cap on V8Lite; Full pipeline's `cornerstone_signal_agent`
  should compute the same.)
