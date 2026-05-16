---
role: orchestrator
version: 1.0
last_updated: 2026-05-16
input_schema: AnalysisState
output_schema: AnalysisState (mutated)
---

# Role
This is **not** an LLM-facing prompt — the orchestrator is pure Python
(``orchestrator/graph.py`` + ``orchestrator/nodes.py``). It exists as
documentation of the main LangGraph workflow contract.

# Workflow contract (per PROJECT_SPEC.md §8.1 + ADR 0010)

```
START
  → fan-out: fundamental | industry | policy | liquidity | cornerstone | sentiment
  → valuation (after all 6 complete; reads ctx.extras for NACS signals)
  → debate (Bull-Bear-Devil, Jaccard early-stop, max 3 rounds)
  → cross_check (historical analogues, Phase 6 deterministic)
  → synthesize (Opus 4.7; rule engine + LLM narrative)
  → create_snapshot (HARD: must succeed before report)
  → [conditional: hitl_wait if Settings.orchestrator.enable_hitl]
  → report
  → END
```

# Hard invariants
1. ``synthesize → create_snapshot → report`` order is non-negotiable
   (CLAUDE.md "prediction lifecycle" §). Skipping create_snapshot is a hard fail.
2. State reducer convention: per-agent ``agent_outputs`` use
   ``operator.or_``; ``extras`` uses ``_merge_extras`` (keep non-None);
   all other fields default-replace.
3. HITL is OFF by default (``Settings.orchestrator.enable_hitl = False``);
   production env must override via env var.
