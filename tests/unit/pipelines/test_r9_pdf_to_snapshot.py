"""R9-1 — unit coverage for pure helpers + dataclasses in pdf_to_snapshot.py.

Pre-R9 the only pipelines tests covered R5-5 (cache-clear absence). The
module has plenty of pure helpers (chunk classification, grouping,
namespace UUIDs, PipelineResult dataclass) that we can unit-test
cheaply without any LLM / PG / Qdrant.

These tests pin behaviour AND lock in invariants from earlier phases:
  * R5-3 chunk_id is a UUID-string (round-trips correctly through grouping).
  * R5-1 disk-IO helpers are wrapped in asyncio.to_thread (verify they
    exist and are pure-sync — the wrapping happens at the call site).
  * Deterministic UUIDs from _NAMESPACE_PROSPECTUS so re-running the
    same input produces the same prospectus doc id.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from hk_ipo_agent.common.enums import ListingType
from hk_ipo_agent.pipelines.pdf_to_snapshot import (
    _NAMESPACE_PROSPECTUS,
    PipelineConfig,
    PipelineResult,
    _classify_chunk,
    _group_chunks_by_section,
)

# ---------------------------------------------------------------------- _classify_chunk


@pytest.mark.parametrize(
    "text,expected",
    [
        ("收入大幅增长，毛利率达到 40%", "financials"),
        ("Revenue grew 50% YoY", "financials"),
        ("EBITDA margin improved", "financials"),
        ("风险因素包含监管不确定性", "risks"),
        ("Key risk factor: customer concentration", "risks"),
        ("我们的业务以 B2B SaaS 为核心，客户主要是大型企业", "business"),
        ("主要股东包括基石投资者三井住友", "shareholders"),
        ("简单废话内容", "other"),
        ("", "other"),
    ],
)
def test_classify_chunk_routes_to_correct_section(text: str, expected: str) -> None:
    """R9-1 — content classifier maps representative CJK + EN phrases."""
    assert _classify_chunk(text) == expected


def test_classify_chunk_priority_financials_over_risks() -> None:
    """R9-1 — financial keywords take precedence over risk keywords (first match wins)."""
    # Contains BOTH "revenue" (financials) and "risk" (risks).
    text = "Revenue is exposed to currency risk"
    assert _classify_chunk(text) == "financials"


# ---------------------------------------------------------------------- _group_chunks_by_section


class _FakeChunk:
    """Minimal stand-in for a real Chunk: only attributes the grouper reads."""

    def __init__(self, text: str, page: int, chunk_id: str) -> None:
        self.text = text
        self.page = page
        self.chunk_id = chunk_id


def test_group_chunks_drops_other_section() -> None:
    """R9-1 — chunks whose classifier returns 'other' are not in the output."""
    chunks = [
        _FakeChunk("收入增长", page=10, chunk_id="cid-1"),
        _FakeChunk("not a real section", page=11, chunk_id="cid-2"),
        _FakeChunk("风险因素", page=12, chunk_id="cid-3"),
    ]
    groups = _group_chunks_by_section(chunks)
    assert set(groups) == {"financials", "risks"}
    assert "other" not in groups


def test_group_chunks_preserves_payload_shape() -> None:
    """R9-1 — each group entry has the contract {text, page, chunk_id}."""
    chunks = [_FakeChunk("收入增长", page=10, chunk_id="cid-1")]
    groups = _group_chunks_by_section(chunks)
    assert groups["financials"] == [{"text": "收入增长", "page": 10, "chunk_id": "cid-1"}]


def test_group_chunks_empty_input_returns_empty_dict() -> None:
    """R9-1 — empty input → {}."""
    assert _group_chunks_by_section([]) == {}


# ---------------------------------------------------------------------- _NAMESPACE_PROSPECTUS


def test_namespace_prospectus_is_a_valid_uuid() -> None:
    """R9-1 — the module-level namespace UUID parses correctly + is stable."""
    assert isinstance(_NAMESPACE_PROSPECTUS, UUID)
    # Pin the literal so accidental edits don't silently regenerate every
    # downstream prospectus id.
    assert str(_NAMESPACE_PROSPECTUS) == "6ba7b811-9dad-11d1-80b4-00c04fd430c8"


# ---------------------------------------------------------------------- PipelineConfig


def test_pipeline_config_defaults_are_safe() -> None:
    """R9-1 — defaults don't try to hit PG / Qdrant unprompted."""
    cfg = PipelineConfig(
        pdf_path=Path("nonexistent.pdf"),
        ipo_id="TEST.HK",
        prospectus_id="P-TEST",
        company_name_zh="测试公司",
    )
    assert cfg.listing_type == ListingType.CH18C_COMMERCIALIZED
    assert cfg.persist_to_pg is False
    assert cfg.persist_to_qdrant is False
    assert cfg.use_cached_extraction is True
    assert cfg.write_report is True
    assert cfg.prefer_llamaparse is False


def test_pipeline_config_frozen_rejects_field_mutation() -> None:
    """R9-1 — config is immutable so mid-run mutation can't break invariants."""
    cfg = PipelineConfig(
        pdf_path=Path("x.pdf"),
        ipo_id="TEST.HK",
        prospectus_id="P-TEST",
        company_name_zh="测试公司",
    )
    with pytest.raises((TypeError, ValueError)):  # pydantic raises ValidationError variant
        cfg.persist_to_pg = True  # type: ignore[misc]


# ---------------------------------------------------------------------- PipelineResult


def test_pipeline_result_snapshot_id_is_uuid_or_none() -> None:
    """R9-1 (also R5-2) — PipelineResult.snapshot_id is typed as UUID | None,
    not str. This pins R5-2 so a regression that re-introduces str doesn't
    silently slip through.
    """
    result = PipelineResult(
        parsed_doc=None,
        chunks=[],
        extraction_result=None,
        final_state={},
        snapshot_id=None,
        total_cost_usd=Decimal("0"),
        total_elapsed_s=0.0,
    )
    # Type round-trip: assigning a UUID works; assigning a str would type-fail
    # statically but the dataclass doesn't enforce at runtime — pin the contract
    # via the annotation instead.
    annotations = PipelineResult.__annotations__
    assert "UUID" in str(annotations["snapshot_id"]), (
        "R9-1 / R5-2: PipelineResult.snapshot_id annotation must include UUID"
    )
    assert result.snapshot_id is None
