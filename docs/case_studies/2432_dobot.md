# 2432.HK — 越疆 (Dobot) Case Study

> Phase 9c case study per ADR 0014. **Winner case** — reproduces v8
> regime-pass empirical pattern (large positive 126d / 252d returns).

## HKEX-disclosed metadata

| Field | Value |
|---|---|
| Stock code | `2432.HK` |
| Company (zh) | 越疆 |
| Listing chapter | **18C-COMM** — cobot / education robotics |
| Industry | 资讯科技业 / 软件服务 / 计算机软硬件 |
| Pricing date | **2024-12-13** |
| Listing date | **2024-12-23** |
| Issue size (HKD) | 830,873,520 |
| Final price (HKD) | 18.80 |
| International oversub | 2.64x |
| Retail oversub | 9.25x |

## Realized post-IPO returns

| Horizon | Return |
|---|---:|
| Day 1 | **+0.84%** |
| Day 22 | +37.54% |
| Day 126 | **+205.70%** (3.06×) |
| Day 252 | **+93.60%** (1.94×) |

**Read**: A canonical v8 winner — modest first day, sharp 22d pop,
explosive 126d (3×), then partial give-back. Validates the **6-month
horizon** as the right calibration target for the regime-pass slice
(matches NACS v8 IC pattern where 180d IC = +0.145 on the canonical
p1_lockup_v2 iteration).

## Cornerstone disclosure

- Disclosed cornerstones at PHIP: **0** (no PG row in
  `cornerstone_investments` for this IPO — either no cornerstone
  tranche or HKEX disclosure not yet ETL'd)
- Cluster Bonus: not applicable

This is consistent with smaller 18C-COMM offerings where the issuer
opts out of formal cornerstone disclosure in favor of anchor / strategic
allocations only.

## V8LiteScorer projection

Reproducible via the same one-liner pattern, `stock_code='2432.HK'` and
`min_pricing_date=date(2024, 12, 1)`.

## Reproducibility recipe — FullPipelineScorer

Same as `2228_quantumpharm.md`:
1. Drop PHIP/AP1 PDF at `data/raw/prospectus/2432_HK_phip.pdf`.
2. Set iFind + ANTHROPIC_API_KEY env vars.
3. Run prospectus extractor.
4. Run FullPipelineScorer.

## Open questions for live run

- Does the **Cornerstone Signal Agent** correctly return a neutral
  signal (not a positive bias) given the lack of disclosure? (V8Lite
  scores 0 bonus; full pipeline should agree.)
- Does the **Fundamental Agent** spot the cobot tailwind in the
  prospectus, given the 18C-COMM threshold revision context (2024-09-01)
  predates pricing?
- Does the **Sentiment Agent** catch the cobot-AI theme co-membership
  (theme_definitions.json has both `cobot` and `embodied_ai`)?
- Did the synthesizer recommend PARTICIPATE? The 3× realized return
  in 6 months is the kind of asymmetric win we want the system to
  not miss.
