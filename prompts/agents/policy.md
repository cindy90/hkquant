---
role: policy_agent
version: 0.0
last_updated: 2026-05-16
input_schema: TBD
output_schema: AgentOutput
inherited_inputs:
  - regime_score              # NACS v7 Regime Gate; see ADR 0005 §2
  - regulatory_regime         # config/regulations/*.yaml
---

# Policy / regulatory regime agent prompt

This prompt MUST instruct the LLM to output `scores.regime_score` so the
valuation ensemble can apply the regime<0 → SKIP truncation (see ADR 0005
§2 and `valuation/ensemble.py`).

Inputs the agent must consult:
- Current `regulatory_regime` (pre/post 2025-08-04; 18C pre/post 2024-09-01)
- `regime_score` data: median 30d return of HK IPOs in
  [pricing_date - 120d, pricing_date - 30d] window (PG `ipo_postmarket`)

TODO (Phase 5): author this prompt per PROJECT_SPEC.md §3.10 + §7 and
ADR 0005 Progress checklist.
