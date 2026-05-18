"""LLM-based structured extraction per PROJECT_SPEC.md §3.5.

Flow:
1. Section router (Sonnet) classifies each chunk into a section type.
2. Per-section extractor (Sonnet) parses chunks into Pydantic sub-models.
3. Failures are retried with Opus before being marked `needs_human_review`.
4. Every Finding carries a citation (page + chunk_id) so the result is
   end-to-end traceable.

Phase 3 lands the orchestration + interface. Real prompts are stub-quality
in Phase 3; Phase 5 will iterate prompt quality once agent feedback loops
provide measurable signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..common.enums import ListingType
from ..common.exceptions import ExtractionError
from ..common.llm_client import LLMClient
from ..common.logging import LogContext, get_logger
from ..common.schemas import (
    Ch18CQualification,
    Citation,
    FinancialSnapshot,
    RiskFactor,
    ShareholderEntry,
)
from ..common.settings import resolve_agent_model
from .schema import ProspectusExtraction

log = get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts" / "extraction"


# ---------------------------------------------------------------------------
# LLM response models (intermediate)
# ---------------------------------------------------------------------------


class _SectionRoute(BaseModel):
    """Output of prospectus_section_router prompt."""

    section: str = Field(
        description="One of: financials / business / risks / shareholders / ch18c / other"
    )
    confidence: float = Field(ge=0.0, le=1.0)


class _FinancialsResponse(BaseModel):
    financials_json: list[dict[str, Any]] = Field(default_factory=list)
    needs_review: bool = False
    notes: str = ""


class _BusinessResponse(BaseModel):
    business_model: str = ""
    revenue_streams: list[dict[str, Any]] = Field(default_factory=list)
    customer_concentration: list[dict[str, Any]] = Field(default_factory=list)
    needs_review: bool = False


class _RisksResponse(BaseModel):
    risk_factors: list[dict[str, Any]] = Field(default_factory=list)
    needs_review: bool = False


class _ShareholdersResponse(BaseModel):
    shareholders: list[dict[str, Any]] = Field(default_factory=list)
    pre_ipo_valuation_rmb: str | None = None  # str-encoded Decimal
    last_round_date: str | None = None
    needs_review: bool = False


class _Ch18CResponse(BaseModel):
    is_commercialized: bool = False
    revenue_threshold_met: bool = False
    rd_intensity_met: bool = False
    market_cap_threshold_hkd: str | None = None  # str-encoded Decimal
    lead_investors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Config + driver
# ---------------------------------------------------------------------------


@dataclass
class ExtractionConfig:
    company_name_zh: str
    listing_type: ListingType
    industry_code: str
    industry_description: str
    use_opus_fallback: bool = True
    max_chunks_per_section: int = 20


@dataclass
class ExtractionResult:
    """Wraps ``ProspectusExtraction`` plus orchestration metadata."""

    extraction: ProspectusExtraction
    total_cost_usd: float
    sections_routed: int
    sections_succeeded: int
    sections_failed: list[str] = field(default_factory=list)


class ProspectusExtractor:
    """Top-level orchestrator. Use one instance per prospectus."""

    def __init__(
        self,
        llm: LLMClient,
        prospectus_id: str,
        *,
        config: ExtractionConfig,
    ) -> None:
        self.llm = llm
        self.prospectus_id = prospectus_id
        self.config = config
        # R4-1: use common.settings.resolve_agent_model as the single
        # entry point so future provider migrations are YAML-only.
        self._llm_routing = resolve_agent_model("extraction.prospectus")
        self._llm_opus = resolve_agent_model("agents.synthesizer")

    async def extract(
        self,
        chunks_by_section: dict[str, list[dict[str, Any]]],
    ) -> ExtractionResult:
        """Run all section extractors and assemble a ProspectusExtraction.

        Args:
            chunks_by_section: precomputed routing — section name -> list of
                chunk payloads ({"text": ..., "page": ..., "chunk_id": ...}).
                Phase 5 will plug in the real router + retriever loop.
        """
        with LogContext(prospectus_id=self.prospectus_id):
            log.info("extraction_starting", sections=list(chunks_by_section.keys()))
            extraction = ProspectusExtraction(
                prospectus_id=self.prospectus_id,
                company_name_zh=self.config.company_name_zh,
                listing_type=self.config.listing_type,
                industry_code=self.config.industry_code,
                industry_description=self.config.industry_description,
                business_model="",
                extraction_version="0.1.0",
                extracted_at=datetime.now(UTC),
            )
            sections_failed: list[str] = []
            sections_succeeded = 0

            for section, chunks in chunks_by_section.items():
                try:
                    await self._extract_section(
                        extraction, section, chunks[: self.config.max_chunks_per_section]
                    )
                    sections_succeeded += 1
                except ExtractionError as exc:
                    log.warning("section_extraction_failed", section=section, error=str(exc))
                    sections_failed.append(section)

            if sections_failed:
                extraction.needs_human_review = True
                extraction.review_reasons = [f"extraction_failed: {s}" for s in sections_failed]

            cost = float(self.llm.cost_log.total_usd())
            log.info(
                "extraction_complete",
                sections_succeeded=sections_succeeded,
                sections_failed=sections_failed,
                cost_usd=cost,
            )
            return ExtractionResult(
                extraction=extraction,
                total_cost_usd=cost,
                sections_routed=len(chunks_by_section),
                sections_succeeded=sections_succeeded,
                sections_failed=sections_failed,
            )

    # ------------------------------------------------------------------ per-section dispatchers

    async def _extract_section(
        self,
        extraction: ProspectusExtraction,
        section: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        if section == "financials":
            await self._extract_financials(extraction, chunks)
        elif section == "business":
            await self._extract_business(extraction, chunks)
        elif section == "risks":
            await self._extract_risks(extraction, chunks)
        elif section == "shareholders":
            await self._extract_shareholders(extraction, chunks)
        elif section == "ch18c":
            await self._extract_ch18c(extraction, chunks)
        else:
            log.debug("section_skipped_unknown_route", section=section)

    async def _extract_financials(
        self,
        extraction: ProspectusExtraction,
        chunks: list[dict[str, Any]],
    ) -> None:
        prompt = self._build_prompt("financials_extractor.md", chunks)
        response = await self._call_with_fallback(prompt, _FinancialsResponse)
        for raw in response.financials_json:
            try:
                snap = FinancialSnapshot.model_validate(raw)
                extraction.financials.append(snap)
            except Exception as exc:
                log.warning("financials_item_skipped", error=str(exc), raw_keys=list(raw.keys()))
                extraction.needs_human_review = True
                extraction.review_reasons.append(f"financials_parse_error: {exc}")
        if response.needs_review:
            extraction.needs_human_review = True
            if response.notes:
                extraction.review_reasons.append(f"financials_note: {response.notes}")
        log.debug(
            "financials_extracted",
            count=len(extraction.financials),
            needs_review=response.needs_review,
        )

    async def _extract_business(
        self,
        extraction: ProspectusExtraction,
        chunks: list[dict[str, Any]],
    ) -> None:
        prompt = self._build_prompt("business_extractor.md", chunks)
        response = await self._call_with_fallback(prompt, _BusinessResponse)
        if response.business_model:
            extraction.business_model = response.business_model
        extraction.revenue_streams.extend(response.revenue_streams)

    async def _extract_risks(
        self,
        extraction: ProspectusExtraction,
        chunks: list[dict[str, Any]],
    ) -> None:
        prompt = self._build_prompt("risks_extractor.md", chunks)
        response = await self._call_with_fallback(prompt, _RisksResponse)
        for raw in response.risk_factors:
            try:
                rf = RiskFactor.model_validate(raw)
                extraction.risk_factors.append(rf)
            except Exception as exc:
                log.warning("risk_item_skipped", error=str(exc))
                extraction.needs_human_review = True
                extraction.review_reasons.append(f"risk_parse_error: {exc}")
        if response.needs_review:
            extraction.needs_human_review = True
        log.debug("risks_extracted", count=len(extraction.risk_factors))

    async def _extract_shareholders(
        self,
        extraction: ProspectusExtraction,
        chunks: list[dict[str, Any]],
    ) -> None:
        prompt = self._build_prompt("shareholders_extractor.md", chunks)
        response = await self._call_with_fallback(prompt, _ShareholdersResponse)
        for raw in response.shareholders:
            try:
                entry = ShareholderEntry.model_validate(raw)
                extraction.shareholders.append(entry)
            except Exception as exc:
                log.warning("shareholder_item_skipped", error=str(exc))
                extraction.needs_human_review = True
                extraction.review_reasons.append(f"shareholder_parse_error: {exc}")
        if response.pre_ipo_valuation_rmb:
            try:
                extraction.pre_ipo_valuation_rmb = Decimal(response.pre_ipo_valuation_rmb)
            except Exception:
                log.warning("pre_ipo_valuation_parse_failed", value=response.pre_ipo_valuation_rmb)
        if response.last_round_date:
            try:
                extraction.last_round_date = date.fromisoformat(response.last_round_date)
            except Exception:
                log.warning("last_round_date_parse_failed", value=response.last_round_date)
        if response.needs_review:
            extraction.needs_human_review = True
        log.debug("shareholders_extracted", count=len(extraction.shareholders))

    async def _extract_ch18c(
        self,
        extraction: ProspectusExtraction,
        chunks: list[dict[str, Any]],
    ) -> None:
        if self.config.listing_type not in {
            ListingType.CH18C_COMMERCIALIZED,
            ListingType.CH18C_PRE_COMMERCIAL,
        }:
            return  # skip — not an 18C listing
        prompt = self._build_prompt("ch18c_qualifier.md", chunks)
        response = await self._call_with_fallback(prompt, _Ch18CResponse)
        try:
            # Build a citation from the first source chunk for traceability.
            first_chunk = chunks[0] if chunks else {}
            citation = Citation(
                page=int(first_chunk.get("page", 1)),
                chunk_id=first_chunk.get("chunk_id"),
            )
            extraction.ch18c_qualification = Ch18CQualification(
                is_commercialized=response.is_commercialized,
                revenue_threshold_met=response.revenue_threshold_met,
                rd_intensity_met=response.rd_intensity_met,
                market_cap_threshold_hkd=Decimal(response.market_cap_threshold_hkd or "0"),
                lead_investors=response.lead_investors,
                citation=citation,
            )
        except Exception as exc:
            log.warning("ch18c_parse_failed", error=str(exc))
            extraction.needs_human_review = True
            extraction.review_reasons.append(f"ch18c_parse_error: {exc}")
        log.debug("ch18c_qualified", commercialized=response.is_commercialized)

    # ------------------------------------------------------------------ internals

    def _build_prompt(self, template_name: str, chunks: list[dict[str, Any]]) -> str:
        """Load the prompt template and append chunk evidence."""
        template_path = PROMPTS_DIR / template_name
        template_text = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
        evidence = "\n\n---\n\n".join(
            f"[Page {c.get('page')}] (chunk_id={c.get('chunk_id')})\n{c.get('text', '')}"
            for c in chunks
        )
        return f"{template_text}\n\n# Source chunks\n\n{evidence}\n"

    async def _call_with_fallback(
        self,
        prompt: str,
        response_model: type[BaseModel],
    ) -> Any:
        """Try Sonnet first, fall back to Opus if validation fails."""
        try:
            return await self.llm.acomplete_json(
                model=self._llm_routing,
                messages=[{"role": "user", "content": prompt}],
                response_model=response_model,
                agent_role="extraction",
                ipo_id=self.prospectus_id,
                max_retries=1,
            )
        except Exception as sonnet_err:
            if not self.config.use_opus_fallback:
                raise ExtractionError(
                    "Extraction failed and Opus fallback disabled",
                    cause=str(sonnet_err),
                ) from sonnet_err
            log.warning("extraction_falling_back_to_opus", reason=str(sonnet_err))
            try:
                return await self.llm.acomplete_json(
                    model=self._llm_opus,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=response_model,
                    agent_role="extraction_fallback",
                    ipo_id=self.prospectus_id,
                    max_retries=1,
                )
            except Exception as opus_err:
                raise ExtractionError(
                    "Extraction failed on both Sonnet and Opus",
                    cause=str(opus_err),
                ) from opus_err


# R4-1: extractor used to have its own _resolve_model helper; superseded by
# common.settings.resolve_agent_model so the project has a single entry point.

__all__ = ("ExtractionConfig", "ExtractionResult", "ProspectusExtractor")
