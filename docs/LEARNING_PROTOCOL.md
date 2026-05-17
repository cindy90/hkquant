# Learning Protocol

> Phase 10 (per ADR 0015) operating protocol for the continuous learning
> loop. **Strict human-review gate is mandatory** — see CLAUDE.md
> prediction-lifecycle constraints.

## Pipeline at a glance

```
prediction_snapshots + outcomes
        |
        v
[10a]  drift_detector  ───┐
       attribution_agg  ──┼──→  [10b]  adjustment_proposer
       counterfactual  ───┘            (writes proposals to
                                       prediction_reviews)
                                              |
                                              v
                                  HUMAN REVIEW (CLI / API)
                                              |
                          accept ←────────────┼──→ reject
                                              v
                                  [10b]  adjustment_applier
                                  + version_manager.bump_version
                                  + 5-IPO sanity backtest
                                              |
                          success ←───────────┼──→ regression / error
                          (IMPLEMENTED)       v
                                       (ROLLBACK + REJECTED)
```

## What auto-proposes vs. what requires human design

The proposer is **deterministic and heuristic** — it can suggest
*where* to look and *what kind of change* to consider, but the
substantive content of any change requires expert judgment.

| AdjustmentType | Auto-propose? | Auto-apply? | Notes |
|---|:---:|:---:|---|
| `WEIGHT_CHANGE`  | yes | no | Proposer suggests target file; reviewer specifies the actual new weight values. Sanity backtest validates. |
| `PROMPT_EDIT`    | yes | no | Proposer points to the prompt file; reviewer drafts the new text. **NEVER auto-edited.** |
| `LOGIC_CHANGE`   | yes | no | Proposer flags the area (e.g. synthesizer trade-off); reviewer designs the change. |
| `FACTOR_ADD`     | yes | no | Proposer signals missing factor; reviewer designs schema + integration. |
| `FACTOR_REMOVE`  | no  | no | Always manual — removing a factor needs governance review. |
| `AGENT_DISABLE`  | no  | no | Always manual — disabling an agent is a system-level decision. |

**Rule**: the proposer never fills in concrete numerical values
or prompt text. It produces structured records pointing the human
reviewer at the right file + diagnosis. The reviewer fills in the
actual proposed value before accepting.

## SLOs

| Step | SLO | Owner |
|---|---|---|
| Drift detection (monthly cycle) | ≤ 30 minutes | Airflow `monthly_learning_dag` (Phase 7.5d) |
| Proposal generation | ≤ 5 minutes | `scripts/run_learning_cycle.py` |
| Human review of pending proposals | ≤ 7 days from creation | Reviewer role |
| Sanity backtest (per applied adjustment) | ≤ 5 minutes | `adjustment_applier` |
| Rollback on regression | immediate (synchronous within applier) | `adjustment_applier` |

A proposal that's been PROPOSED for > 14 days without review is
auto-escalated to `senior_reviewer` via `alerts.py`. After 30 days
it's auto-archived (status=REJECTED + notes "expired").

## Hard rules (CLAUDE.md prediction-lifecycle binding)

1. **No silent mutations.** The applier is the **only** code that can
   write to `config/` or `prompts/`. All other modules (proposer,
   detector, aggregator, counterfactual, version_manager) are
   read-only with respect to those paths.

2. **No apply without ACCEPTED + reviewer.** The applier reads the
   parent `prediction_reviews` row directly from PG before any disk
   write and raises `AdjustmentNotApprovedError` if:
   - `reviewer` field is empty / null, OR
   - `adjustment_status != ACCEPTED`.

3. **Every apply bumps a version.** `version_manager.bump_version`
   writes a new `config_versions` row FIRST (before disk write) so
   we have an audit anchor even if the disk write fails.

4. **Sanity backtest before commit.** The applier runs a 5-IPO
   walk-forward on the modified config and rejects the apply if the
   new IC drops by more than `rebacktest_ic_tolerance` (default 0.03)
   vs. the baseline.

5. **Rollback creates a new row.** We **never** delete or modify
   prior `config_versions` rows. Rollback writes a *new* row with
   `change_type="rollback"` carrying the prior content.

6. **PROMPT_EDIT requires explicit reviewer-supplied content.** The
   proposer never drafts prompt text — only points to the file. The
   reviewer must supply `proposed_value["text"]` in the review row
   before accepting.

## Monthly cycle: end-to-end recipe

```bash
# 1. Run diagnostics + propose (typically scheduled by Airflow):
uv run python scripts/run_learning_cycle.py --window-days 90

# Output:
#   - reports/learning/{YYYY-MM}_learning_report.md
#   - 0 or more PROPOSED rows in prediction_reviews

# 2. Reviewer triages pending proposals:
uv run python scripts/review_proposals.py list

# 3. Reviewer accepts (after designing the concrete change):
uv run python scripts/review_proposals.py accept <review_id> --reviewer alice \
    --notes "Reduced 18C-COMM dcf weight from 0.35 to 0.30"

# 4. Operator applies (will run sanity backtest + auto-rollback on regression):
uv run python -c "
import asyncio
from uuid import UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.learning_loop.adjustment_applier import AdjustmentApplier
async def main():
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        applier = AdjustmentApplier(session_factory=sf)
        result = await applier.apply_review(UUID('<review-id>'))
        print(result)
    finally:
        await engine.dispose()
asyncio.run(main())
"

# 5. (After window) Re-backtest to confirm the change held:
uv run python scripts/run_backtest.py --min-date <accepted_date>
```

## Alerts

- **proposal_pending_overdue** (> 14 days): warning to `reviewer` queue
- **proposal_pending_critical** (> 30 days): critical to `senior_reviewer`
- **applier_rollback_triggered**: critical to `operator` + `senior_reviewer`
- **drift_signal_severity_critical**: warning to `senior_reviewer`

All alerts carry `actionable_info` per CLAUDE.md alerts rule —
"failed" alone is never acceptable; alerts must say what to do next.

## What the cycle does NOT do

- **Does NOT auto-LLM-generate prompt edits.** Phase 11+ may add LLM-
  assisted prompt drafting; until then PROMPT_EDIT is purely human.
- **Does NOT modify `prediction_snapshots`.** Immutability is enforced
  by DB trigger (Phase 7.5a).
- **Does NOT touch the orchestrator graph topology.** Logic changes to
  `orchestrator/edges.py` or `nodes.py` are out of scope — they would
  require an ADR + version bump.
- **Does NOT auto-roll forward.** If a rollback happens, the system
  stays on the rolled-back version until a human re-accepts a new
  proposal.
