"""Phase 10c — interactive proposal review CLI per ADR 0015 §10c.

Lists proposed reviews from PG and lets a human accept / reject / apply
them. Updates the ``prediction_reviews.adjustment_status`` field + sets
the reviewer column.

Usage::

    uv run python scripts/review_proposals.py list
    uv run python scripts/review_proposals.py accept <review_id> --reviewer alice
    uv run python scripts/review_proposals.py reject <review_id> --reviewer alice --reason "weights too aggressive"

    # R3-8: ``apply`` lands the accepted proposal through AdjustmentApplier,
    # writes the new config file, and runs the 5-IPO sanity backtest.
    uv run python scripts/review_proposals.py apply <review_id> \\
        --proposed-content path/to/new_weights.json \\
        --applied-by alice
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import AdjustmentStatus
from hk_ipo_agent.common.exceptions import AdjustmentNotApprovedError
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.learning_loop.adjustment_applier import AdjustmentApplier


async def _list_pending(sf) -> int:
    from hk_ipo_agent.data.models import PredictionReviewRow

    async with sf() as s:
        stmt = (
            select(PredictionReviewRow)
            .where(PredictionReviewRow.adjustment_status == AdjustmentStatus.PROPOSED.value)
            .order_by(PredictionReviewRow.created_at.desc())
            .limit(50)
        )
        rows = list((await s.execute(stmt)).scalars().all())
    if not rows:
        print("[review] no pending proposals")
        return 0
    print(f"[review] {len(rows)} pending proposals:")
    for row in rows:
        proposals = row.proposed_adjustments or []
        print(
            f"  {row.id} | snap={str(row.snapshot_id)[:8]} | "
            f"n_proposals={len(proposals)} | created={row.created_at.date()}"
        )
        for i, p in enumerate(proposals[:3]):  # first 3
            t = p.get("target_path", "—")
            kind = p.get("adjustment_type", "—")
            print(f"      [{i}] {kind:18s} → {t}")
    return 0


async def _accept(sf, review_id: UUID, reviewer: str, notes: str) -> int:
    from hk_ipo_agent.data.models import PredictionReviewRow

    async with sf() as s:
        row = await s.get(PredictionReviewRow, review_id)
        if row is None:
            print(f"[review] review {review_id} not found", file=sys.stderr)
            return 2
        if row.adjustment_status != AdjustmentStatus.PROPOSED.value:
            print(
                f"[review] cannot accept: review {review_id} status is "
                f"{row.adjustment_status}, requires 'proposed'",
                file=sys.stderr,
            )
            return 3
        await s.execute(
            update(PredictionReviewRow)
            .where(PredictionReviewRow.id == review_id)
            .values(
                adjustment_status=AdjustmentStatus.ACCEPTED.value,
                reviewer=reviewer,
                notes_md=notes or row.notes_md,
                updated_at=datetime.now(UTC),
            )
        )
        await s.commit()
    print(f"[review] accepted {review_id} by {reviewer}")
    print("[review] next: run adjustment_applier.apply_review(review_id)")
    return 0


async def _apply(
    sf,
    review_id: UUID,
    *,
    applied_by: str,
    proposed_content_path: Path | None,
    proposal_index: int,
) -> int:
    """R3-8 — apply an accepted review's proposal through AdjustmentApplier.

    The applier enforces the strict human gate (reviewer non-empty +
    status=ACCEPTED) and writes both the config_versions audit row and
    the on-disk file. R3-7 requires concrete content via
    ``--proposed-content`` when the proposal's proposed_value is None.
    """
    proposed_content: dict[str, Any] | None = None
    if proposed_content_path is not None:
        if not proposed_content_path.exists():
            print(
                f"[review] --proposed-content path does not exist: {proposed_content_path}",
                file=sys.stderr,
            )
            return 4
        try:
            proposed_content = json.loads(proposed_content_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[review] --proposed-content is not valid JSON: {exc}", file=sys.stderr)
            return 4

    applier = AdjustmentApplier(session_factory=sf)
    try:
        result = await applier.apply_review(
            review_id,
            proposal_index=proposal_index,
            proposed_content=proposed_content,
            applied_by=applied_by,
        )
    except AdjustmentNotApprovedError as exc:
        print(f"[review] human-gate violation: {exc}", file=sys.stderr)
        return 3
    except KeyError as exc:
        print(f"[review] {exc}", file=sys.stderr)
        return 2

    if result.success:
        print(
            f"[review] applied {review_id} → {result.target_path} @ {result.applied_version} "
            f"(baseline_ic={result.baseline_ic} new_ic={result.new_ic})"
        )
        return 0
    print(
        f"[review] apply FAILED for {review_id}: {result.reason}",
        file=sys.stderr,
    )
    return 5


async def _reject(sf, review_id: UUID, reviewer: str, reason: str) -> int:
    from hk_ipo_agent.data.models import PredictionReviewRow

    async with sf() as s:
        row = await s.get(PredictionReviewRow, review_id)
        if row is None:
            print(f"[review] review {review_id} not found", file=sys.stderr)
            return 2
        await s.execute(
            update(PredictionReviewRow)
            .where(PredictionReviewRow.id == review_id)
            .values(
                adjustment_status=AdjustmentStatus.REJECTED.value,
                reviewer=reviewer,
                notes_md=f"REJECTED by {reviewer}: {reason}",
                updated_at=datetime.now(UTC),
            )
        )
        await s.commit()
    print(f"[review] rejected {review_id}: {reason}")
    return 0


async def _amain(args: argparse.Namespace) -> int:
    engine = create_async_engine(get_settings().database.url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        if args.cmd == "list":
            return await _list_pending(sf)
        if args.cmd == "accept":
            return await _accept(sf, args.review_id, args.reviewer, args.notes or "")
        if args.cmd == "reject":
            return await _reject(
                sf, args.review_id, args.reviewer, args.reason or "no reason given"
            )
        if args.cmd == "apply":
            return await _apply(
                sf,
                args.review_id,
                applied_by=args.applied_by,
                proposed_content_path=args.proposed_content,
                proposal_index=args.proposal_index,
            )
        print(f"[review] unknown command: {args.cmd}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 10c proposal review CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List pending proposals")
    accept = sub.add_parser("accept", help="Accept a proposal")
    accept.add_argument("review_id", type=UUID)
    accept.add_argument("--reviewer", required=True)
    accept.add_argument("--notes", type=str, default=None)
    reject = sub.add_parser("reject", help="Reject a proposal")
    reject.add_argument("review_id", type=UUID)
    reject.add_argument("--reviewer", required=True)
    reject.add_argument("--reason", type=str, default=None)

    # R3-8 — apply subcommand: routes through AdjustmentApplier (human
    # gate enforced inside). LEARNING_PROTOCOL §accept used to require
    # users to hand-roll ``python -c "import asyncio; ..."`` — now this
    # CLI is the canonical entry point.
    apply_p = sub.add_parser(
        "apply", help="Apply an accepted proposal (writes config + runs sanity backtest)"
    )
    apply_p.add_argument("review_id", type=UUID)
    apply_p.add_argument(
        "--proposed-content",
        type=Path,
        default=None,
        help=(
            "Path to JSON file containing the new config content. "
            "Required when the proposal's proposed_value is None (R3-7)."
        ),
    )
    apply_p.add_argument("--applied-by", required=True, help="Reviewer / operator name")
    apply_p.add_argument(
        "--proposal-index",
        type=int,
        default=0,
        help="Which proposal in the review's proposed_adjustments list to apply (default 0)",
    )

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
