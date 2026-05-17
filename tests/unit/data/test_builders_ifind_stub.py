"""R3-1 — iFind補漏 paths must fail-loud, not silently return zeros.

Pre-R3-1 ``HistoricalIPOLoader._upsert_from_ifind`` and
``ComparablePoolBuilder._ingest`` returned ``(0, 0)`` / ``0`` whenever
called. Combined with ``ifind=None`` being the audit-only mode, this
meant a caller that intended to fetch real data but forgot to wire in
the SDK silently got an empty result and proceeded as if "no new IPOs".

R3-1 selects PLAN option B: keep ``ifind=None`` audit-only mode (which
ADR 0005 documents); but if a caller passes a real ifind client and
hits the stub path, raise ``NotImplementedError`` so the silently-wrong
case is impossible. The actual wiring is deferred to ADR 0018, tracked
in docs/PLAN_post_v1.0.md §5.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hk_ipo_agent.data.builders.comparable_pool_builder import ComparablePoolBuilder
from hk_ipo_agent.data.builders.historical_ipo_loader import HistoricalIPOLoader


@pytest.mark.asyncio
async def test_historical_ipo_loader_with_ifind_raises_not_implemented_in_stub_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3-1 — calling _upsert_from_ifind directly must raise NotImplementedError.

    The full load_listed_between path also calls the stub, but it touches
    PG; this test exercises the stub method in isolation so it stays
    unit-level (no PG required).
    """
    ifind = MagicMock()
    ifind.get_ipo_history = AsyncMock(return_value=object())
    loader = HistoricalIPOLoader(ifind=ifind)
    with pytest.raises(NotImplementedError, match="iFind"):
        await loader._upsert_from_ifind(
            ifind_result=object(),
            existing_codes=set(),
            repo=MagicMock(),
        )


@pytest.mark.asyncio
async def test_comparable_pool_builder_ingest_raises_not_implemented() -> None:
    """R3-1 — same fail-loud contract for ComparablePoolBuilder._ingest."""
    ifind = MagicMock()
    builder = ComparablePoolBuilder(ifind=ifind)
    with pytest.raises(NotImplementedError, match="iFind"):
        await builder._ingest(ifind_result=object(), repo=MagicMock())
