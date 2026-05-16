"""IPO list / detail endpoints per PROJECT_SPEC.md §16.2."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...common.enums import ListingType
from ...prediction_registry.registry import get_registry
from ..auth import CurrentUserDep
from ..schemas import IPOListItem, PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/api/ipos", tags=["ipos"])


@router.get("/", response_model=PaginatedResponse)
async def list_ipos(
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    """List IPOs derived from existing snapshots (latest per IPO)."""
    _ = user
    snapshots = await get_registry().list_snapshots()
    latest_by_ipo: dict[str, IPOListItem] = {}
    for snap in sorted(snapshots, key=lambda s: s.created_at, reverse=True):
        ipo_key = str(snap.ipo_id)
        if ipo_key in latest_by_ipo:
            continue
        ext = snap.input_data_snapshot.get("extraction", {})
        listing_type_str = ext.get("listing_type") or ListingType.MAINBOARD_TECH.value
        try:
            listing_type = ListingType(listing_type_str)
        except ValueError:
            listing_type = ListingType.MAINBOARD_TECH
        latest_by_ipo[ipo_key] = IPOListItem(
            ipo_id=ipo_key,
            company_name_zh=ext.get("company_name_zh", ""),
            company_name_en=ext.get("company_name_en"),
            stock_code=ext.get("stock_code"),
            listing_type=listing_type,
            industry_code=ext.get("industry_code", ""),
            decision=snap.decision.decision,
            overall_score=snap.decision.scorecard.get("overall"),
        )
    items = list(latest_by_ipo.values())
    page = items[offset : offset + limit]
    return PaginatedResponse(
        data=[i.model_dump(mode="json") for i in page],
        meta=PaginationMeta(
            total=len(items),
            limit=limit,
            offset=offset,
            has_next=offset + limit < len(items),
        ),
    )


__all__ = ("router",)
