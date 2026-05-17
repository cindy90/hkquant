# 2533.HK — 黑芝麻智能 (Black Sesame International) Case Study

> Phase 9c case study per ADR 0014.

## HKEX-disclosed metadata

| Field | Value |
|---|---|
| Stock code | `2533.HK` |
| Company (zh) | 黑芝麻智能 |
| Listing chapter | **18C-COMM** — first 18C-commercialized mass-market chip listing |
| Industry | 资讯科技业 / 软件服务 / 计算机软硬件 |
| Pricing date | **2024-07-31** |
| Listing date | **2024-08-08** |
| Issue size (HKD) | 1,036,000,000 |
| Final price (HKD) | 28.00 |
| International oversub | 1.05x (just covered) |
| Retail oversub | 2.52x (lukewarm) |

## Realized post-IPO returns

| Horizon | Return |
|---|---:|
| Day 1 | **-1.47%** |
| Day 22 | +9.78% |
| Day 126 | **-6.80%** |
| Day 252 | -1.22% |

**Read**: Modest first-day, brief month-1 pop, then settled negative
by D+126 and flat by D+252. The 22d → 126d retracement is the canonical
"weak-cornerstone IPO" pattern that the v8 Cluster Bonus filter is
designed to flag (only 2 cornerstones disclosed).

## Cornerstone disclosure

- Disclosed cornerstones at PHIP: **2** investors
- Cluster (≥2 SPVs sharing ultimate_holder): **no** — both unique

Per ADR 0005 §2, cornerstone < 2 = no Cluster Bonus, which V8LiteScorer
applies symmetrically (smaller bonus). The full pipeline's
`cornerstone_signal_agent` will downgrade signal_strength accordingly.

## V8LiteScorer projection

Reproducible via the same one-liner pattern as `2228_quantumpharm.md`,
substituting `stock_code='2533.HK'` and `min_pricing_date=date(2024, 7, 1)`.

## Reproducibility recipe — FullPipelineScorer

Same as `2228_quantumpharm.md`:
1. Drop PHIP/AP1 PDF at `data/raw/prospectus/2533_HK_phip.pdf`.
2. Set iFind + ANTHROPIC_API_KEY env vars.
3. Run prospectus extractor.
4. Run FullPipelineScorer + compare to realized returns above.

## Open questions for live run

- Will the Sentiment Agent's **AI Gilding** detector flag this name?
  (Auto chip / ADAS sits squarely in the 2024 AI theme; ADR 0005 §2's
  AI revenue < 10% × 0.85 discount applies.)
- Does the Policy Agent's regime_score for 2024-07-30 (pre-2025-08-04
  pricing rule) match `regulatory_regime_for(2024-07-30) == PRE_20250804`?
- Does the synthesizer recommend SKIP given the thin cornerstone
  signal? V8LiteScorer's deterministic score is informative but not
  conclusive — the LLM debate is where the call gets made.
