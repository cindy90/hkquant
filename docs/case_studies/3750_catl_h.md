# 3750.HK — 宁德时代 (CATL H-Share) Case Study

> Phase 9c case study per ADR 0014. **Mega-cap AH dual-listing**
> reference case — exercises the AH listing_type code path even
> though the ETL recorded it as MB-OTHER (NACS classification
> predates the AH enum addition).

## HKEX-disclosed metadata

| Field | Value |
|---|---|
| Stock code | `3750.HK` |
| Company (zh) | 宁德时代 |
| Listing chapter | **MB-OTHER** (NACS classification — re-classified to AH in Phase 9 ETL refresh once spec is finalized) |
| Industry | 工业(HS) / 工业工程(HS) / 能源生产装备(HS) |
| Pricing date | **2025-05-12** |
| Listing date | **2025-05-20** |
| Issue size (HKD) | **41,005,723,900** (~5.3B USD — largest 2025 HK IPO) |
| Final price (HKD) | 263.00 |
| International oversub | 15.17x (very strong) |
| Retail oversub | 151.15x (extremely strong) |

## Realized post-IPO returns

| Horizon | Return |
|---|---:|
| Day 1 | **+0.91%** |
| Day 22 | +9.27% |
| Day 126 | **+61.37%** |
| Day 252 | (not yet observed — listed 2025-05-20, latest snapshot from ETL window) |

**Read**: AH-style steady appreciation — flat first day, mid-single-
digit month 1, strong 6-month. Pattern is consistent with the A-share
parent's institutional ownership / continuous price discovery (the H
share takes time to converge to the A-share's existing valuation).

## Cornerstone disclosure

- Disclosed cornerstones at PHIP: **23** investors (massive cornerstone book)
- Cluster (≥2 SPVs sharing ultimate_holder): **likely yes** (typical
  mega-cap AH with multiple ICBC / CIC vehicles)

V8LiteScorer caps `cluster_bonus` at +0.20 regardless of count, but the
full pipeline's `cornerstone_signal_agent` will surface the depth and
diversity of the cornerstone book as a separate **conviction signal**.

## V8LiteScorer projection

Reproducible via the standard one-liner, `stock_code='3750.HK'` and
`min_pricing_date=date(2025, 5, 1)`.

## Reproducibility recipe — FullPipelineScorer

Same as `2228_quantumpharm.md`:
1. Drop PHIP/AP1 PDF at `data/raw/prospectus/3750_HK_phip.pdf`.
2. Set iFind + ANTHROPIC_API_KEY env vars.
3. **Important**: the Phase 5 `AHPremiumModel` needs the A-share
   pair_code wired into `ipo_events.ah_pair_a_code` (NACS legacy
   may have left this null — Phase 9 spot fix may be needed).
4. Run prospectus extractor.
5. Run FullPipelineScorer.

## Open questions for live run

- **AH listing_type detection**: Does the Phase 5 `valuation_agent`
  correctly route to the `AHPremiumModel` despite the ETL having
  recorded `MB-OTHER`? Spec calls for AH detection by `ah_pair_a_code IS NOT NULL`.
- **Policy Agent regime**: 2025-05-11 anchor is **pre-2025-08-04**
  new pricing rules. Does `regulatory_regime_for(2025-05-11)` return
  `PRE_20250804` correctly?
- **Industry Agent**: Battery / EV-supply-chain tailwind; does the
  agent flag the policy support correctly?
- **Synthesizer / Bull-Bear debate**: With 151× retail oversub the
  Bull-Bear should be lopsided to Bull — does the Devil agent
  meta-question whether the AH discount-arb crowd is overheating?

## Notes on the rule-set discontinuity

Listed **before** 2025-08-04 new pricing rules → Mechanism A/B not
applicable; 35% clawback rule not applicable. The case study is
useful precisely for that — it's the **last anchor** of the
pre-2025-08 regime within the ETL window, paired with the upcoming
post-regime cases that 9c+1 (Phase 10 case studies) will add.
