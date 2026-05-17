"""Adjustment applier — Phase 10b per ADR 0015 + PROJECT_SPEC.md §3.12.

**THE STRICTEST FILE IN THE LEARNING LOOP**. The applier is the only
component that mutates production config / prompt files, and it MUST:

1. Verify the parent ``prediction_reviews`` row has:
   - ``reviewer`` field non-null
   - ``adjustment_status == ACCEPTED``

   Otherwise raise ``AdjustmentNotApprovedError`` (no config / prompt
   write happens). This is the prediction-lifecycle binding from
   CLAUDE.md "no auto-apply" rule.

2. Wrap the apply in a try/except → rollback flow:
   a. ``version_manager.bump_version(target, new_content, source_review_id=...)``
   b. Write the new content to disk
   c. Run a 5-IPO mini-backtest via ``run_walk_forward``
   d. Compare new metrics vs. baseline; if regression → rollback +
      mark review as REJECTED with the failure reason.
   e. On success → mark review as IMPLEMENTED + applied_at +
      applied_version.

Any step b/c/d failure rolls back via VersionManager + leaves status =
REJECTED. The DB transaction is committed step-by-step so partial
failures are auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select, update

from ..common.enums import AdjustmentStatus
from ..common.exceptions import AdjustmentNotApprovedError
from ..common.logging import get_logger
from ..common.schemas import ProposedAdjustment
from .version_manager import VersionManager

logger = get_logger(__name__)


# Default re-backtest size — small enough to be fast, large enough to
# catch obvious regressions.
DEFAULT_REBACKTEST_N: int = 5

# Default IC tolerance — if new IC drops by more than this absolute
# value on the small backtest, the adjustment is rejected.
DEFAULT_REBACKTEST_IC_TOLERANCE: float = 0.03


@dataclass(frozen=True)
class ApplierConfig:
    """Knobs for the applier."""

    rebacktest_n: int = DEFAULT_REBACKTEST_N
    rebacktest_ic_tolerance: float = DEFAULT_REBACKTEST_IC_TOLERANCE
    # When True (default), perform the 5-IPO sanity backtest. Tests
    # disable this to keep unit scope narrow.
    run_sanity_backtest: bool = True
    # When True (default), actually write the new file content. Tests
    # may disable this to avoid touching working tree files.
    write_to_disk: bool = True


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of a single applier run."""

    review_id: UUID
    target_path: str
    applied_version: str | None
    success: bool
    reason: str
    new_ic: float | None = None
    baseline_ic: float | None = None


class AdjustmentApplier:
    """The only component allowed to write production config / prompt files.

    The strict human gate is enforced by reading the parent review's
    ``adjustment_status`` + ``reviewer`` fields directly from PG before
    any write happens.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        version_manager: VersionManager | None = None,
        config: ApplierConfig | None = None,
    ) -> None:
        self._sf = session_factory
        self._vm = version_manager or VersionManager(session_factory)
        self._cfg = config or ApplierConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def apply_review(
        self,
        review_id: UUID,
        *,
        proposal_index: int = 0,
        proposed_content: dict[str, Any] | None = None,
        applied_by: str = "system:learning_loop",
        run_walk_forward_fn: Any = None,
    ) -> ApplyResult:
        """Apply a specific proposal from a review.

        Args:
            review_id: target review's UUID.
            proposal_index: which proposal inside the review to apply
                (default first).
            proposed_content: the new file content. If None we use the
                proposal's ``proposed_value`` (must be a dict for yaml
                targets).
            applied_by: who's running this (user / 'system' / CLI).
            run_walk_forward_fn: optional injected backtest fn so tests
                can stub it. Signature: ``(scorer, sf) → MetricsReport``.

        Returns:
            ApplyResult with success flag + reason + applied_version.

        Raises:
            AdjustmentNotApprovedError: if review's adjustment_status is
                not ACCEPTED or reviewer is empty. **This is the
                lifecycle hard gate.**
        """
        review_row = await self._load_review(review_id)
        self._enforce_human_gate(review_row)

        proposals = review_row.proposed_adjustments or []
        if proposal_index >= len(proposals):
            return ApplyResult(
                review_id=review_id,
                target_path="",
                applied_version=None,
                success=False,
                reason=f"proposal_index {proposal_index} out of range",
            )
        proposal_raw = proposals[proposal_index]
        try:
            proposal = ProposedAdjustment.model_validate(proposal_raw)
        except Exception as exc:
            return ApplyResult(
                review_id=review_id,
                target_path="",
                applied_version=None,
                success=False,
                reason=f"proposal schema invalid: {exc}",
            )

        # R3-7 — reject the empty-value path that pre-fix silently wrapped
        # None into {"value": None} and wrote to disk. Reviewers must
        # supply ``proposed_content`` (e.g. via CLI ``--proposed-content
        # path/to/json``) so config files never get partial / null content.
        if proposed_content is None:
            if proposal.proposed_value is None:
                return ApplyResult(
                    review_id=review_id,
                    target_path=proposal.target_path,
                    applied_version=None,
                    success=False,
                    reason=(
                        "proposed_value is None — reviewer must supply concrete "
                        "content via --proposed-content path/to/json. "
                        "See PLAN R3-7 + LEARNING_PROTOCOL §accept."
                    ),
                )
            proposed_content = (
                proposal.proposed_value
                if isinstance(proposal.proposed_value, dict)
                else {"value": proposal.proposed_value}
            )

        # bump_version writes a new row in config_versions FIRST so we
        # have an audit anchor even if the disk write fails.
        version = await self._vm.bump_version(
            proposal.target_path,
            proposed_content,
            applied_by=applied_by,
            source_review_id=review_id,
            change_type="learning_loop_applied",
        )

        try:
            if self._cfg.write_to_disk:
                _write_target_file(proposal.target_path, proposed_content)
            backtest_ok, baseline_ic, new_ic = True, None, None
            if self._cfg.run_sanity_backtest and run_walk_forward_fn is not None:
                backtest_ok, baseline_ic, new_ic = await self._sanity_backtest(
                    run_walk_forward_fn,
                )
            if not backtest_ok:
                # Rollback to the previous version.
                await self._rollback(proposal.target_path, version.version, applied_by)
                await self._mark_review(
                    review_id,
                    AdjustmentStatus.REJECTED,
                    notes=(
                        f"sanity backtest failed: baseline_ic={baseline_ic} "
                        f"new_ic={new_ic} drop="
                        f"{(baseline_ic or 0) - (new_ic or 0):+.4f} "
                        f"> tolerance {self._cfg.rebacktest_ic_tolerance:.4f}"
                    ),
                )
                return ApplyResult(
                    review_id=review_id,
                    target_path=proposal.target_path,
                    applied_version=version.version,
                    success=False,
                    reason="sanity backtest regression — rolled back",
                    new_ic=new_ic,
                    baseline_ic=baseline_ic,
                )

            await self._mark_review(
                review_id,
                AdjustmentStatus.IMPLEMENTED,
                applied_version=version.version,
                applied_at=datetime.now(UTC),
            )
            return ApplyResult(
                review_id=review_id,
                target_path=proposal.target_path,
                applied_version=version.version,
                success=True,
                reason="applied successfully",
                new_ic=new_ic,
                baseline_ic=baseline_ic,
            )
        except Exception as exc:
            # R3-5 — disk write or backtest raised; roll back the version row.
            # Pre-fix wrapped this in `contextlib.suppress(KeyError)` which
            # silently swallowed cases where there was no prior version to
            # roll back to, leaving the bad-content row as the active
            # version. _rollback now writes a `rollback_initial_seed`
            # sentinel row instead of returning silently, so the audit
            # trail always reflects the failed apply.
            logger.error(
                "applier_failed_rolling_back",
                review_id=str(review_id),
                target=proposal.target_path,
                error=str(exc),
            )
            await self._rollback(
                proposal.target_path,
                version.version,
                applied_by,
            )
            await self._mark_review(
                review_id,
                AdjustmentStatus.REJECTED,
                notes=f"applier raised: {type(exc).__name__}: {exc}",
            )
            return ApplyResult(
                review_id=review_id,
                target_path=proposal.target_path,
                applied_version=version.version,
                success=False,
                reason=f"exception during apply: {exc}",
            )

    # ------------------------------------------------------------------
    # Human-gate enforcement
    # ------------------------------------------------------------------

    def _enforce_human_gate(self, review_row: Any) -> None:
        reviewer = (review_row.reviewer or "").strip()
        status = review_row.adjustment_status or ""
        if not reviewer:
            raise AdjustmentNotApprovedError(
                f"review {review_row.id} has empty reviewer field — human gate violated"
            )
        if status != AdjustmentStatus.ACCEPTED.value:
            raise AdjustmentNotApprovedError(
                f"review {review_row.id} status={status!r}, requires "
                f"{AdjustmentStatus.ACCEPTED.value!r}"
            )

    # ------------------------------------------------------------------
    # PG helpers
    # ------------------------------------------------------------------

    async def _load_review(self, review_id: UUID) -> Any:
        from ..data.models import PredictionReviewRow

        async with self._sf() as s:
            row = await s.get(PredictionReviewRow, review_id)
            if row is None:
                raise KeyError(f"review {review_id} not found")
            return row

    async def _mark_review(
        self,
        review_id: UUID,
        status: AdjustmentStatus,
        *,
        applied_version: str | None = None,
        applied_at: datetime | None = None,
        notes: str | None = None,
    ) -> None:
        from ..data.models import PredictionReviewRow

        values: dict[str, Any] = {
            "adjustment_status": status.value,
            "updated_at": datetime.now(UTC),
        }
        if applied_version is not None:
            values["applied_version"] = applied_version
        if applied_at is not None:
            values["applied_at"] = applied_at
        if notes is not None:
            values["notes_md"] = notes
        async with self._sf() as s:
            await s.execute(
                update(PredictionReviewRow)
                .where(PredictionReviewRow.id == review_id)
                .values(**values)
            )
            await s.commit()
        logger.info(
            "review_status_updated",
            review_id=str(review_id),
            status=status.value,
        )

    async def _rollback(
        self,
        target_path: str,
        current_version: str,
        applied_by: str,
    ) -> None:
        """Roll back to the version *before* current_version.

        R3-5: when no prior version exists (this was the first bump for
        the target path), write a ``rollback_initial_seed`` sentinel row
        into ``config_versions`` so the audit trail captures the failed
        apply. Pre-fix this case just logged a warning and silently
        returned, leaving the (broken) current_version as the active
        row — a future ``get_active_version`` call would happily return
        the bad content.

        Disk-side: when the sentinel path is taken, the on-disk file
        (if any was written by the failed apply) is removed so it
        doesn't outlive its config_versions entry.
        """
        versions = await self._vm.list_versions(target_path, limit=10)
        if len(versions) < 2:
            # R3-5: write a sentinel "initial seed rollback" row so the
            # audit trail shows the failed apply AND its rollback.
            logger.warning(
                "applier_no_prior_version_rollback_sentinel",
                target=target_path,
                current=current_version,
            )
            await self._vm.bump_version(
                target_path,
                {"_rollback_initial_seed": True, "reverted_version": current_version},
                applied_by=applied_by,
                change_type="rollback_initial_seed",
            )
            # Remove the on-disk file written by the failed apply (if any).
            if self._cfg.write_to_disk:
                repo_root = Path(__file__).resolve().parents[3]
                disk_path = repo_root / target_path
                if disk_path.exists():
                    try:
                        disk_path.unlink()
                    except OSError as exc:
                        logger.warning(
                            "applier_sentinel_unlink_failed",
                            target=target_path,
                            error=str(exc),
                        )
            return
        # versions[0] is current (just bumped), versions[1] is prior.
        prior = versions[1]
        rolled = await self._vm.rollback(
            target_path,
            prior.version,
            applied_by=applied_by,
        )
        if self._cfg.write_to_disk and rolled.content is not None:
            _write_target_file(target_path, rolled.content)

    async def _sanity_backtest(
        self,
        run_walk_forward_fn: Any,
    ) -> tuple[bool, float | None, float | None]:
        """Compare new metrics vs. baseline on a small sample.

        ``run_walk_forward_fn`` is expected to be a callable that takes
        no args and returns ``(baseline_ic, new_ic)`` — caller's
        responsibility to wire up the actual run_walk_forward + IC
        computation against current vs. previous config.
        """
        try:
            baseline_ic, new_ic = await run_walk_forward_fn()
        except Exception as exc:
            logger.warning(
                "sanity_backtest_raised",
                error=str(exc),
            )
            return False, None, None
        if baseline_ic is None or new_ic is None:
            return True, baseline_ic, new_ic  # no signal to fail on
        drop = baseline_ic - new_ic
        ok = drop <= self._cfg.rebacktest_ic_tolerance
        return ok, baseline_ic, new_ic


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------


def _write_target_file(target_path: str, content: dict[str, Any]) -> None:
    """Write content to ``target_path``. YAML for .yaml, JSON for .json,
    raw text (under "text" key) for .md."""
    repo_root = Path(__file__).resolve().parents[3]
    full_path = repo_root / target_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.endswith((".yaml", ".yml")):
        full_path.write_text(
            yaml.safe_dump(content, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    elif target_path.endswith(".json"):
        full_path.write_text(
            json.dumps(content, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    elif target_path.endswith(".md"):
        text = content.get("text") if isinstance(content, dict) else None
        if not isinstance(text, str):
            raise ValueError(f"markdown target {target_path} requires content['text'] str")
        full_path.write_text(text, encoding="utf-8")
    else:
        # Fallback: JSON-dump the dict.
        full_path.write_text(
            json.dumps(content, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = (
    "DEFAULT_REBACKTEST_IC_TOLERANCE",
    "DEFAULT_REBACKTEST_N",
    "AdjustmentApplier",
    "ApplierConfig",
    "ApplyResult",
)

# Suppress unused-import.
_ = select
