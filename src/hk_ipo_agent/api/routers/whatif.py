"""What-If endpoint per PROJECT_SPEC.md §16.9 + ADR 0011."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ...common.enums import Permission
from ...prediction_registry.registry import get_registry
from ...synthesizer import run_whatif
from ..auth.dependencies import CurrentUser, require_permission
from ..schemas import WhatIfRequest, WhatIfResponse

router = APIRouter(prefix="/api/whatif", tags=["whatif"])


@router.post("/run", response_model=WhatIfResponse)
async def whatif_run(
    payload: WhatIfRequest,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.RUN_WHATIF))],
) -> WhatIfResponse:
    _ = user
    try:
        snapshot = await get_registry().get_snapshot(payload.snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"snapshot {payload.snapshot_id} not found",
        ) from exc
    return await run_whatif(snapshot, payload.modified_assumptions)


__all__ = ("router",)
