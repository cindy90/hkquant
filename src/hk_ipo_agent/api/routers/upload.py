"""PDF upload endpoint — POST /api/upload/prospectus.

Accepts a prospectus PDF via multipart/form-data, saves it to disk,
and triggers the pdf_to_snapshot pipeline as a background task.
Returns 202 immediately with identifiers for tracking.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict

from ...common.enums import ListingType, Permission, RealtimeEventType
from ...common.logging import get_logger
from ..auth.dependencies import CurrentUser, require_permission
from ..streaming.event_bus import get_event_bus

logger = get_logger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])

# Where uploaded PDFs are stored.
_DATA_ROOT = Path(__file__).resolve().parents[4] / "data"
_PROSPECTUS_DIR = _DATA_ROOT / "prospectuses"

# PDF magic bytes (first 4 bytes = %PDF).
_PDF_MAGIC = b"%PDF"
_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


class UploadResponse(BaseModel):
    """202 response after successful upload acceptance."""

    model_config = ConfigDict(extra="forbid")

    prospectus_id: str
    ipo_id: str
    run_id: str
    accepted_at: datetime


@router.post(
    "/prospectus",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_prospectus(
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.TRIGGER_ANALYSIS))],
    file: UploadFile = File(..., description="招股书 PDF 文件"),
    stock_code: str = Form(..., description="股票代码，如 6871.HK"),
    company_name: str = Form(..., description="公司名称（中文）"),
    listing_type: str = Form("18C-COMM", description="上市类型"),
    industry_code: str = Form("unknown", description="行业代码"),
    as_of_date: str | None = Form(None, description="分析日期 (YYYY-MM-DD)，默认今天"),
) -> UploadResponse:
    """Upload a prospectus PDF and trigger analysis pipeline.

    The file is validated (PDF magic bytes + content type), saved to disk,
    and the pdf_to_snapshot pipeline is started as a background task.
    """
    _ = user  # consumed by dependency for auth

    # --- Validate file ---
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件必须为 PDF 格式（.pdf 后缀）",
        )

    if file.content_type and file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Content-Type 必须为 application/pdf，收到: {file.content_type}",
        )

    # Read file header to verify magic bytes
    header = await file.read(16)
    if not header.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容不是有效的 PDF（magic bytes 校验失败）",
        )
    # Reset to beginning
    await file.seek(0)

    # --- Validate listing_type ---
    try:
        lt = ListingType(listing_type)
    except ValueError:
        valid = [e.value for e in ListingType]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"listing_type 无效，可选值: {valid}",
        ) from None

    # --- Parse as_of_date ---
    # TODO: wire `analysis_date` through to _run_pipeline_background so
    # MarketData(as_of_date=...) honours the caller-supplied value instead
    # of always using date.today(). Tracked as a separate fix; for now we
    # parse to validate the format and surface 400s on bad input.
    analysis_date: date
    if as_of_date:
        try:
            analysis_date = date.fromisoformat(as_of_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="as_of_date 格式必须为 YYYY-MM-DD",
            ) from None
    else:
        analysis_date = date.today()
    _ = analysis_date  # ack: parsed for validation; not yet plumbed to background task

    # --- Generate IDs ---
    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    prospectus_id = f"{stock_code}_{ts}"
    ipo_id = stock_code
    run_id = str(uuid4())

    # --- Save file to disk ---
    _PROSPECTUS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = _PROSPECTUS_DIR / f"{prospectus_id}.pdf"

    content = await file.read()
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小超过限制 ({_MAX_FILE_SIZE // (1024 * 1024)} MB)",
        )

    save_path.write_bytes(content)

    # --- Schedule background pipeline ---
    background.add_task(
        _run_pipeline_background,
        pdf_path=save_path,
        ipo_id=ipo_id,
        prospectus_id=prospectus_id,
        company_name=company_name,
        listing_type=lt,
        industry_code=industry_code,
        run_id=run_id,
    )

    return UploadResponse(
        prospectus_id=prospectus_id,
        ipo_id=ipo_id,
        run_id=run_id,
        accepted_at=datetime.now(UTC),
    )


async def _run_pipeline_background(
    *,
    pdf_path: Path,
    ipo_id: str,
    prospectus_id: str,
    company_name: str,
    listing_type: ListingType,
    industry_code: str,
    run_id: str,
) -> None:
    """Background task: run pdf_to_snapshot pipeline and emit SSE events."""
    bus = get_event_bus()

    # Emit start event
    await bus.publish(
        RealtimeEventType.SCHEDULER_STARTED,
        payload={"ipo_id": ipo_id, "run_id": run_id, "kind": "upload_analysis"},
    )

    try:
        from datetime import date as date_type

        from ...common.llm_client import LLMClient
        from ...pipelines import PipelineConfig, run_pdf_to_snapshot
        from ...valuation.base import MarketData

        config = PipelineConfig(
            pdf_path=pdf_path,
            ipo_id=ipo_id,
            prospectus_id=prospectus_id,
            company_name_zh=company_name,
            listing_type=listing_type,
            industry_code=industry_code,
            write_report=True,
            persist_to_pg=True,
            persist_to_qdrant=True,
        )

        # Construct MarketData with defaults. The pipeline gracefully
        # degrades when peer_multiples / regime_score are unavailable.
        market_data = MarketData(
            as_of_date=date_type.today(),
            listing_type=listing_type,
        )

        llm_client = LLMClient()

        await run_pdf_to_snapshot(
            config,
            market_data,
            llm_client=llm_client,
            log=lambda msg: None,  # suppress in background
        )

        await bus.publish(
            RealtimeEventType.SCHEDULER_COMPLETED,
            payload={"ipo_id": ipo_id, "run_id": run_id, "kind": "upload_analysis"},
        )

    except Exception as exc:
        # Surface stack trace to logs — pre-fix this was silently swallowed
        # because event_bus persistence wasn't wired (lifespan didn't call
        # set_event_bus), so SCHEDULER_FAILED events vanished if no SSE
        # client was actively subscribed. With main.py lifespan now wiring
        # PG-backed EventBus + this logger.exception, every failed upload
        # leaves both a row in ``realtime_events`` AND a full traceback
        # in the API stdout / structured log.
        logger.exception(
            "upload_pipeline_failed",
            ipo_id=ipo_id,
            run_id=run_id,
            prospectus_id=prospectus_id,
            error=str(exc)[:500],
        )
        await bus.publish(
            RealtimeEventType.SCHEDULER_FAILED,
            payload={
                "ipo_id": ipo_id,
                "run_id": run_id,
                "kind": "upload_analysis",
                "error": str(exc)[:500],
            },
        )


__all__ = ("router",)
