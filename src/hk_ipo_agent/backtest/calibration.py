"""Bayesian weight calibration for valuation ensemble.

Inherits from NACS v8 per ADR 0005 §3 — v8 iteration baselines:
- 5 legacy iteration archives at `data/derived/backtest/iterations/` (p1_10
  → p2_2) capture the v8 IC progression. Each new candidate parameter set
  MUST not significantly degrade these baselines on the same samples
  (monotonicity constraint).
- Calibration uses Bayesian optimization over `config/valuation_weights.yaml`
  (per ListingType / industry slice), constrained by:
    (a) sum-to-1 weight constraint
    (b) monotonicity vs v8 baseline IC
    (c) sample size > 20 per slice (avoid overfitting; n≈400 total)
- Output: new versioned `valuation_weights.yaml` written via
  `learning_loop/version_manager.py` (NOT applied directly — see CLAUDE.md
  prediction-lifecycle constraints).

TODO (Phase 8): implement per PROJECT_SPEC.md §3.9 and ADR 0005 Progress checklist.
"""
