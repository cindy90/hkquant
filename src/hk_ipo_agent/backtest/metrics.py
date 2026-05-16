"""Hit rate / IC / Sharpe metrics for walk-forward backtest.

Inherits from NACS v8 per ADR 0005 §3 — IC framework:
- Rank IC: Spearman rank correlation between predicted decision score and
  realized return. v8 mainboard 60d IC = +0.057 (all samples) / +0.121
  (regime≥0 filter). Effective threshold: > +0.05 acceptable, > +0.10 strong.
- L-S Spread: top-decile mean return minus bottom-decile mean return.
- t-stat: statistical significance of L-S spread. v8 regime≥0 subsample
  achieved t = +2.41 (significant). Threshold: > 1.5 marginal, > 2.0 robust.
- Hit rate: % of "participate" decisions with positive 60d return.
- Decision accuracy: combined skill across price-range hit + decision-correct.

These three are the canonical metrics for Phase 8 calibration; new candidates
must clear them (v8 baseline) before being accepted as improvements.

TODO (Phase 8): implement per PROJECT_SPEC.md §3.9 and ADR 0005 Progress checklist.
"""
