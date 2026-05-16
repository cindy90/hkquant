"""What-If endpoint per PROJECT_SPEC.md §16.9 + ADR 0011 + ADR 0012 §7.5b-3.

CLAUDE.md v1.2.1 constraint: "What-If results MUST be persisted to
whatif_calculations" so Phase 10 attribution can compare assumptions
to outcomes. Persistence is best-effort: a DB write failure logs at
WARNING and still returns the computed response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from ...common.enums import Permission
from ...common.logging import get_logger
from ...data.database import async_session_factory
from ...data.models import WhatIfCalculationRow
from ...prediction_registry.registry import get_registry
from ...synthesizer import run_whatif
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import WhatIfRequest, WhatIfResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/whatif", tags=["whatif"])


@router.post("/run", response_model=WhatIfResponse)
async def whatif_run(
    payload: WhatIfRequest,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.RUN_WHATIF))],
) -> WhatIfResponse:
    try:
        snapshot = await get_registry().get_snapshot(payload.snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"snapshot {payload.snapshot_id} not found",
        ) from exc
    response = await run_whatif(snapshot, payload.modified_assumptions)
    # user_id is intentionally NOT persisted in whatif_calculations — the
    # user_accounts FK can't be satisfied for the JWT-issued admin / test
    # tokens that don't have a user_accounts row. Audit middleware
    # already records user context separately; whatif_calculations.user_id
    # is only useful when SSO is wired (Phase 9) and lifespan provisions
    # users in PG up-front.
    _ = user
    await _persist_calculation(snapshot_id=payload.snapshot_id, user_id=None,
                               payload=payload, response=response)
    return response


async def _persist_calculation(
    *,
    snapshot_id,
    user_id,
    payload: WhatIfRequest,
    response: WhatIfResponse,
) -> None:
    """Best-effort INSERT into whatif_calculations. Never raises.

    Persistence is intentionally non-blocking: if PG is down we still
    serve the caller (matches Phase 7's spirit; outcome attribution can
    re-derive from on-the-fly inputs if needed).
    """
    row = WhatIfCalculationRow(
        id=uuid4(),
        snapshot_id=snapshot_id,
        user_id=user_id,
        modified_assumptions=payload.modified_assumptions,
        original_distribution=response.original_distribution.model_dump(mode="json"),
        new_distribution=response.new_distribution.model_dump(mode="json"),
        cost_usd=response.cost_usd,
        runtime_ms=response.runtime_ms,
        created_at=datetime.now(UTC),
    )
    try:
        async with async_session_factory()() as s:
            s.add(row)
            await s.commit()
    except Exception as exc:
        logger.warning(
            "whatif_persist_failed",
            snapshot_id=str(snapshot_id), error=str(exc),
        )


__all__ = ("router",)
