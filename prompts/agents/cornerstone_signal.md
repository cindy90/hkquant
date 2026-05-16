---
role: cornerstone_signal_agent
version: 0.0
last_updated: 2026-05-16
input_schema: TBD
output_schema: AgentOutput
inherited_inputs:
  - cluster_bonus_multiplier   # NACS v7 Cluster Bonus; see ADR 0005 §2
  - cornerstone_profiles       # 1,314 base; ADR 0005 §1
  - sponsor_track_record       # 24m win rate
---

# Cornerstone signal agent prompt

This prompt MUST instruct the LLM to detect industry-capital syndicates by
looking up `ultimate_holder` clustering from the cornerstone profiles
knowledge base (ADR 0005 §2 — Cluster Bonus). When ≥2 cornerstones share
the same ultimate_holder, surface this as a high-confidence finding with
the historical effect size (60d mean +22% vs no-cluster +14%, std ↓40%).

Inputs the agent must consult:
- Predicted cornerstone profiles (`data/builders/cornerstone_profile_builder.py`)
- Sponsor track record (`data/builders/sponsor_track_record.py`)
- Last-round investors disclosed in prospectus (extraction `shareholders`)

TODO (Phase 5): author this prompt per PROJECT_SPEC.md §3.10 + §7 and
ADR 0005 Progress checklist.
