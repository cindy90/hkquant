"""Phase 10c — interactive proposal review CLI per ADR 0015 §10c.

Lists proposed reviews from PG and lets a human accept / reject them.
Updates the ``prediction_reviews.adjustment_status`` field + sets the
reviewer column. Apply is a separate step (``adjustment_applier``).

Usage::

    uv run python scripts/review_proposals.py list
    uv run python scripts/review_proposals.py accept <review_id> --reviewer alice
    uv run python scripts/review_proposals.py reject <review_id> --reviewer alice --reason "weights too aggressive"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hk_ipo_agent.common.enums import AdjustmentStatus
from hk_ipo_agent.common.settings import get_settings


async def _list_pending(sf) -> int:
    from hk_ipo_agent.data.models import PredictionReviewRow  # noqa: PLC0415

    async with sf() as s:
        stmt = (
            select(PredictionReviewRow)
            .where(
                PredictionReviewRow.adjustment_status == AdjustmentStatus.PROPOSED.value
            )
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
    from hk_ipo_agent.data.models import PredictionReviewRow  # noqa: PLC0415

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


async def _reject(sf, review_id: UUID, reviewer: str, reason: str) -> int:
    from hk_ipo_agent.data.models import PredictionReviewRow  # noqa: PLC0415

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
            return await _accept(
                sf, args.review_id, args.reviewer, args.notes or ""
            )
        if args.cmd == "reject":
            return await _reject(
                sf, args.review_id, args.reviewer, args.reason or "no reason given"
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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
