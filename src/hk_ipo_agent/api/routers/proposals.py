"""proposals router — Phase 7.5/8 deferred per ADR 0011.

Stubs return 501 Not Implemented so OpenAPI surface is complete.
Real implementations land when their backing data layers are ready.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@router.get("/")
async def _not_implemented() -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="proposals endpoints deferred to a later Phase per ADR 0011",
    )


__all__ = ("router",)
