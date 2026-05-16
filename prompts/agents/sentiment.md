---
role: sentiment_agent
version: 0.0
last_updated: 2026-05-16
input_schema: TBD
output_schema: AgentOutput
inherited_inputs:
  - theme_heat                 # heat_today.json; see ADR 0005 §2 + §5
  - theme_history_30d          # history.csv
  - premium_curve              # premium_curve.json
  - ai_gilding_signal          # ai_revenue_manual.json + extraction
  - theme_taxonomy             # theme_definitions.json
---

# Sentiment agent prompt

This prompt MUST instruct the LLM to consult the migrated theme tracker
outputs (ADR 0005 §5):
- Theme heat (`themes/heat_today.json` → `data/knowledge_base/themes/heat_today.json`)
- 30d trend (`themes/history.csv`)
- Premium curve (`themes/premium_curve.json`)
- AI gilding detector: if company claims AI exposure but AI revenue share
  <10%, surface as a high-severity narrative-risk finding (×0.85 NACS
  empirical multiplier — let Synthesizer aggregate, do not apply locally).

Inputs the agent must consult:
- Company theme matching via `theme_definitions.json` keywords + core companies
- Contemporaneous IPO pricing/aftermarket warmth (PG `ipo_postmarket` recent)
- Media sentiment (`data/sources/news_client.py`)

TODO (Phase 5): author this prompt per PROJECT_SPEC.md §3.10 + §7 and
ADR 0005 Progress checklist.
