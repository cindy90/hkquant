"""Weighted ensemble across single valuation models.

Inherits from NACS v8 per ADR 0005 §2 — Regime Gate post-adjustment:
- After ensemble weighting (per `config/valuation_weights.yaml`), apply
  Regime Gate truncation: if `policy_agent` output contains
  `regime_score < 0`, force the ensemble decision to SKIP (regardless of
  per-model valuations). Rationale: NACS v7 empirics show NACS model itself
  is invalid in adverse market regimes (60d IC drops to ~0); the decision
  threshold MUST flip rather than try to "value through" a hostile regime.
- This is a hard gate, not a soft penalty — implementation MUST check
  regime_score in `synthesizer/decision_engine.py` AND in the ensemble
  post-processor here for defense-in-depth.

Other post-adjustments to consider (legacy NACS post_adjustments chain;
re-evaluate empirical effectiveness in Phase 4):
- chapter_18c: ×0.70 high-valuation penalty
- secondary_listing: ×0.85
- ah_hedge: tiered multipliers
- small_cap_cs_rescue: ×0.90

TODO (Phase 4): implement per PROJECT_SPEC.md §3.7 and ADR 0005 Progress checklist.
"""
