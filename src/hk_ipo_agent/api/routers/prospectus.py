"""Prospectus PDF serving + citation lookup per PROJECT_SPEC.md §16.10.

Phase 7 MVP: returns PDFs from a configurable local directory (settings
``prospectus.storage_dir`` if present, else ``data/prospectuses/``).
Signed URLs / S3 / R2 deferred to Phase 9 (ADR 0011).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ...common.enums import Permission
from ...common.settings import get_settings
from ..auth.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/api/prospectus", tags=["prospectus"])


def _prospectus_path(prospectus_id: str) -> Path:
    settings = get_settings()
    base = (settings.data_dir / "prospectuses").resolve()
    return base / f"{prospectus_id}.pdf"


@router.get("/{prospectus_id}.pdf")
async def get_prospectus_pdf(
    prospectus_id: str,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.READ_PROSPECTUS))],
) -> FileResponse:
    """Stream a local prospectus PDF. 404 if not present.

    R6-1: gated behind READ_PROSPECTUS.
    """
    _ = user
    # Reject path-traversal attempts.
    if "/" in prospectus_id or "\\" in prospectus_id or ".." in prospectus_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid prospectus_id",
        )
    path = _prospectus_path(prospectus_id)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"prospectus {prospectus_id} not found",
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
    )


__all__ = ("router",)
