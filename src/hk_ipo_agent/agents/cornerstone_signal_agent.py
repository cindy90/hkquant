"""Cornerstone signal agent: predicted cornerstone strength + sponsor quality.

Inherits from NACS v8 per ADR 0005 §2 — Cluster Bonus:
- NACS v7 Cluster Bonus empirically validated: when ≥2 cornerstones share the
  same `ultimate_holder` (industry-capital syndicate via multiple SPVs), apply
  scoring multiplier ×1.10 / ×1.15 / ×1.20 (cluster size 2 / 3 / ≥4). Result:
  cluster≥2 IPOs had 60d mean +22% (vs no-cluster +14%), std reduced ~40%.
- Data source: `data/builders/cornerstone_profile_builder.py` exposes
  ultimate_holder grouping over the migrated 1,314-investor knowledge base
  (ADR 0005 §1).
- This agent MUST contribute a `cluster_bonus_multiplier` finding to
  `predicted_cornerstone_strength` score (final aggregation is the
  Synthesizer's job, not multiplicative inside this agent).

Other dimensions:
- Predicted cornerstone signal: based on historical cornerstone profiles
  (sovereign / strategic / hedge / family office / etc.) and their past
  6-month return-after-lockup track record.
- Sponsor quality: `data/builders/sponsor_track_record.py` 24-month win rate
  and avg post-listing performance.

TODO (Phase 5): implement per PROJECT_SPEC.md §7 and ADR 0005 Progress checklist.
"""
