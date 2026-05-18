# Architecture — HK IPO Cornerstone Investment Agent

> **Audience**: new contributors trying to understand the system end-to-end,
> and operators trying to deploy it. For the formal spec see
> [PROJECT_SPEC.md](../PROJECT_SPEC.md); for UI integration see
> [PROJECT_SPEC_UI.md](../PROJECT_SPEC_UI.md); for the rationale behind
> specific design choices see [decisions/](decisions/).
>
> **Last refreshed**: R10-7 (post-v1.0 `v1.0.10`).

---

## 1. 30-second pitch

The system ingests a Hong Kong IPO prospectus PDF, runs it through 7
specialist LLM agents (fundamental / industry / policy / liquidity /
cornerstone / sentiment / valuation), debates Bull vs Bear vs Devil,
synthesizes a final SUBSCRIBE/PARTIAL/SKIP decision, persists an
immutable prediction snapshot, then tracks outcomes at 11 fixed
checkpoints (T+1, +5, +10, +22, +30, +60, +90, +126, +180, +252, +360).
Drift triggers automatic adjustment proposals, which a human reviews
and applies through a controlled propose → review → apply pipeline
that auto-rolls-back on regression.

---

## 2. Module map

```
src/hk_ipo_agent/
├── common/                  schemas, enums, settings, llm_client, logging
├── data/                    SQLAlchemy ORM + Alembic migrations + repos + builders
│   ├── models/              ipo_events / cornerstone_* / sponsors / prediction_*
│   ├── repositories/        BaseRepository + per-entity wrappers
│   ├── builders/            HistoricalIPOLoader / CornerstoneProfileBuilder / etc.
│   ├── sources/             ifind_client + stubs (disclosure / news / web_search)
│   └── migrations/versions/ alembic revisions
├── prospectus/              parser (LlamaParse + PyMuPDF) → chunker → embeddings → vector_store
├── agents/                  7 specialist agents + base + workflow_extras
├── valuation/               DCF + comparable + monte_carlo + ensemble
├── critic/                  Bull-Bear-Devil debate + cross_check + Jaccard early stop
├── synthesizer/             Opus synthesizer + whatif
├── orchestrator/            LangGraph main graph + nodes + edges + state + checkpoint
├── prediction_registry/     snapshot + outcome_tracker + review_workflow + alerts + state machine
│   ├── ipo_lifecycle/       state_detectors + state_machine + stale_detector + terminal_handlers
│   └── schedulers/          high_freq / daily / event_driven + airflow_dags/
├── learning_loop/           drift_detector + attribution + counterfactual + adjustment_proposer + applier + version_manager + reports
├── reporting/               memo builder + PDF/DOCX exporter + charts
├── pipelines/               pdf_to_snapshot end-to-end (5-step: parse → chunk → extract → graph → report)
├── backtest/                walk-forward runner + metrics (Rank IC / L-S / t-stat) + calibration + regime_detection
└── api/                     FastAPI (routers / middleware / auth / websocket / streaming)
```

---

## 3. End-to-end data flow

### 3.1 Analysis path (new IPO)

```
1. PDF arrives                           scripts/analyze_pdf.py
   ↓
2. prospectus.parser                      LlamaParse primary / PyMuPDF fallback
   ↓ ParsedDocument (blocks + tables + figures)
3. prospectus.chunker                     section-aware 1.5k-char chunks; UUID5 chunk_id
   ↓ list[Chunk]
4. prospectus.vector_store                Qdrant upsert (per-prospectus collection)
   ↓
5. prospectus.extractor                   LLM extraction → ProspectusExtraction
   ↓
6. orchestrator.build_main_graph          LangGraph: 7 agents fanout → valuation → debate → cross_check → synthesize
   ↓ FinalDecision
7. prediction_registry.create_snapshot    immutable PG row (DB trigger prevents UPDATE/DELETE)
   ↓
8. reporting.build_memo_markdown          memo + PDF + DOCX exports
```

### 3.2 Outcome tracking path (continuous)

```
DailyScheduler (02:00-03:00 HKT)
  ↓
For each LISTED snapshot, for each due CHECKPOINT_DAY:
  ↓
1. BenchmarkPriceService.get_trading_day_offset  R8-7: trading-day, not calendar-day
   ↓
2. outcome_tracker.track                          stock return + HSI/HSTECH/industry benchmarks
   ↓ PredictionOutcome row
3. (if major checkpoint) review_workflow.generate_draft
   ↓ PredictionReview row (status=PROPOSED)

Plus: stale_detector scan + state-machine transition + terminal_handler.
At T+360, R8-3: emit CRITICAL alert (NOT auto-terminate; operator gates).
```

### 3.3 Learning path (monthly)

```
scripts/run_learning_cycle.py  (Airflow monthly DAG)
  ↓
1. drift_detector (CUSUM + PSI 4 sub-detectors)   → DriftSignal[]
   ↓
2. attribution_aggregator + counterfactual         → per-agent attribution
   ↓
3. adjustment_proposer                              → ProposedAdjustment, written to prediction_reviews
   ↓
4. HUMAN GATE — scripts/review_proposals.py        list / accept / reject
   ↓
5. After accept: adjustment_applier.apply_review   sanity backtest (5 IPOs) → apply OR auto-rollback
   ↓
6. version_manager.bump_version                    pg_advisory_xact_lock; full history retained
```

---

## 4. Key extension points

| You want to … | Edit / add | Notes |
|---|---|---|
| add a new specialist agent | `src/hk_ipo_agent/agents/<name>_agent.py` + `prompts/agents/<name>.md` | register in `orchestrator/nodes.make_nodes` + `prompts/<name>.md` frontmatter |
| add a new valuation method | `src/hk_ipo_agent/valuation/<name>.py` + register in `valuation/ensemble` | wire to `config/valuation_weights.yaml` |
| add a new external data source | `src/hk_ipo_agent/data/sources/<name>.py` | follow `disclosure_scraper.py` Protocol+Stub pattern (R7-2) |
| add a new lifecycle state | `common/enums.IPOLifecycleStateType` + `VALID_TRANSITIONS` | R9-5: must NOT introduce backward edges; pin in test_r9_state_machine_no_rewind |
| add a new prompt | `prompts/<dir>/<role>.md` with `version` + `inherited_inputs` frontmatter | R4-7: `inherited_inputs` MUST land in `agents/base._verify_inherited_inputs` alias map |
| add a new API endpoint | `src/hk_ipo_agent/api/routers/<name>.py` + register in `routers/__init__.ALL_ROUTERS` | R6-1: every endpoint MUST gate on `require_permission(<perm>)` |

---

## 5. Deployment

### 5.1 Production stack (per CLAUDE.md §自动化与状态机约束)

| Component | Tech | Purpose |
|---|---|---|
| API | FastAPI + Uvicorn workers | OpenAPI 3.1, SSE for live progress, WS for chat |
| LangGraph orchestrator | embedded in API or run via worker | the 7-agent debate + valuation pipeline |
| LLM | KIMI/Moonshot (ADR 0017 supersedes ADR 0002 Claude) | OpenAI-compatible chat completions |
| Vector store | Qdrant 1.10+ | per-prospectus collection isolation (R5-3 UUID point ids) |
| RDBMS | PostgreSQL 16 | snapshots + outcomes + reviews + alerts + audit + chat + whatif |
| Cache / queue | Redis 7+ | rate-limit window, SSE multiplex, scheduler advisory locks |
| Scheduler | **Airflow (prod)** / APScheduler (dev) | 4 DAGs: high_freq / daily / alert_dispatch / monthly_learning |
| File parser | LlamaParse (primary) + PyMuPDF (fallback) | dual-track per ADR 0004 |

### 5.2 Boot sequence (FastAPI lifespan)

```python
async def lifespan(app: FastAPI):
    # 1. Build the LLMClient (R6-6: prod fails fast on missing key)
    app.state.llm_client = LLMClient(daily_budget_usd=Decimal("100"))

    # 2. Install PG-backed prediction registry (production) — bypassed in tests
    set_registry(PGPredictionRegistry())

    # 3. Seed default in-memory users into user_accounts (R6-7 whatif FK)
    await _upsert_seed_accounts_into_pg()

    yield
```

### 5.3 Required env vars (production)

| Variable | Purpose | Hard-checked at startup? |
|---|---|---|
| `KIMI_API_KEY` | LLM credentials | R6-6 — prod reraise on missing |
| `HK_IPO__ENVIRONMENT` | prod / production triggers all prod-guards | R2-1 / R2-7 / R6-6 / R8-5 |
| `HK_IPO__ORCHESTRATOR__ENABLE_HITL` | must = true in prod | R2-1 |
| `HK_IPO__AUTH__JWT_SECRET` | must NOT be the default placeholder | R2-7 |
| `HK_IPO__SCHEDULER__BACKEND` | must = `airflow` in prod | R8-5 |
| `HK_IPO__DATABASE__*` | PG connection (host/port/name/user/password) | engine fails on first query |
| `QDRANT_URL` + optional `QDRANT_API_KEY` | vector store | per-prospectus collection check |

---

## 6. The hardening journey (R0–R9)

The system shipped as v1.0 in 2026-05-17 with 10 phases (Phase 0 →
Phase 10). 8 review agents then surfaced 24 Critical + ~60 Major
issues; the R0–R11 post-v1.0 hardening plan ([docs/PLAN_post_v1.0.md](PLAN_post_v1.0.md))
tracks the systematic fix. As of this document refresh, R0–R9 are
complete (`v1.0.9`); R10–R11 are in progress.

Key invariants that the hardening locked in:

- **Citations are never fabricated** (R1-3): the `Finding.citations`
  list with `min_length=1` is enforced at Pydantic-validate time; the
  legacy `Citation(page=1)` fallback in `agents/base.py` is gone.
- **Snapshots are immutable** (R2-3 + Phase 7.5a DB trigger): the ORM
  refuses UPDATE/DELETE; PG `prevent_snapshot_modification()` trigger
  is the defense-in-depth backstop.
- **Regime gate is a hard gate** (R8-1): missing fixture raises, no
  silent 0.0 that flips the SKIP gate into all-pass mode.
- **T+360 is operator-gated** (R8-3): scheduler emits CRITICAL alert,
  never auto-transitions to TERMINATED. CLAUDE.md §自动化与状态机约束:
  "超时不等于失败".
- **Calibration honesty** (R3-3 + R9-4): when calibration is a no-op
  (V8LiteScorer / tied IC), `is_placebo=True` AND `chosen_weights ==
  baseline`. No spurious "calibration moved weights" in YAML.
- **State machine never rewinds** (R9-5): VALID_TRANSITIONS goes
  forward only; corrections use the explicit `record_correction` path
  with audit log + bypass flag.

---

## 7. Where to read next

- [PROJECT_SPEC.md](../PROJECT_SPEC.md) — formal spec (v1.2.1)
- [PROJECT_SPEC_UI.md](../PROJECT_SPEC_UI.md) — frontend / API contract (v1.3)
- [decisions/README.md](decisions/README.md) — ADR index (0001 – 0018)
- [PLAN_post_v1.0.md](PLAN_post_v1.0.md) — full hardening roadmap R0 – R11
- [LEARNING_PROTOCOL.md](LEARNING_PROTOCOL.md) — monthly learning-loop runbook
- [CHANGELOG.md](../CHANGELOG.md) — release notes per tag
