# 9660.HK — 地平线机器人-W (Horizon Robotics) Case Study

> Phase 9c case study per ADR 0014. Strong-trajectory case — large
> 22d pop + sustained 126d / 252d return.

## HKEX-disclosed metadata

| Field | Value |
|---|---|
| Stock code | `9660.HK` |
| Company (zh) | 地平线机器人-W |
| Listing chapter | **MB-OTHER** (WVR structure; W suffix) |
| Industry | 资讯科技业 / 软件服务 / 计算机软硬件 |
| Pricing date | **2024-10-16** |
| Listing date | **2024-10-24** |
| Issue size (HKD) | 6,086,881,458 |
| Final price (HKD) | 3.99 |
| International oversub | 13.81x |
| Retail oversub | 33.83x |

## Realized post-IPO returns

| Horizon | Return |
|---|---:|
| Day 1 | **+16.34%** (strong first-day pop) |
| Day 22 | +1.71% (give-back) |
| Day 126 | **+70.24%** |
| Day 252 | **+71.88%** (held the gain) |

**Read**: Day 1 sets the pop, market gives back over month 1, then
re-rates to +70% by D+126 and holds. The "auto AI chip" theme
sustains the bid — exactly the kind of regime-pass winner the v8
180d slice was optimized for.

## Cornerstone disclosure

- Disclosed cornerstones at PHIP: **4** investors
- Cluster (≥2 SPVs sharing ultimate_holder): inspect
  `cornerstone_investments` for shared ultimate_holder strings — at
  least one shared parent is typical for this issuer (auto OEM
  consortium).

V8LiteScorer applies cluster bonus when count ≥ 2 with shared parent;
full pipeline's `cornerstone_signal_agent` returns the explicit
breakdown.

## V8LiteScorer projection

Reproducible via the standard one-liner, `stock_code='9660.HK'` and
`min_pricing_date=date(2024, 10, 1)`.

## Reproducibility recipe — FullPipelineScorer

Same as `2228_quantumpharm.md`:
1. Drop PHIP/AP1 PDF at `data/raw/prospectus/9660_HK_phip.pdf`.
2. Set iFind + ANTHROPIC_API_KEY env vars.
3. Run prospectus extractor.
4. Run FullPipelineScorer.

## Open questions for live run

- **WVR (`-W`) handling**: Does the Phase 5 `policy_agent` correctly
  flag the dual-class share structure? The spec calls for explicit
  WVR risk disclosure in the report.
- **Cornerstone Signal**: Does the auto-OEM cornerstone consortium
  trigger Cluster Bonus (NACS empirics: 22% mean 60d return vs 14%
  baseline)?
- **Industry Agent — auto AI chip thesis**: Does the agent surface
  the policy tailwind (国产替代) without being overcredulous?
- **Sentiment Agent**: AI Gilding detector — is auto AI chip
  classified as core revenue or as gilding? (Horizon's actual auto
  AI revenue share is high — this is NOT a gilded story.)
- **Synthesizer**: Given the strong Day-1 pop, does the system avoid
  the "bought-at-the-top" trap? (Subscription strategy matters: the
  call would be PARTIAL at IPO, not chase on Day 1.)
