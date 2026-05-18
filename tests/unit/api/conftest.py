"""Shared fixtures for the API test suite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hk_ipo_agent.api.auth.audit_middleware import reset_audit_store_for_test
from hk_ipo_agent.api.auth.dependencies import (
    _seed_defaults,  # type: ignore[attr-defined]
    reset_users_for_test,
)
from hk_ipo_agent.api.auth.jwt import issue_access_token
from hk_ipo_agent.api.main import create_app
from hk_ipo_agent.api.routers.alerts import reset_alert_store_for_test
from hk_ipo_agent.api.streaming.event_bus import reset_event_bus_for_test
from hk_ipo_agent.api.websocket import reset_chat_store_for_test
from hk_ipo_agent.common.enums import (
    AgentRole,
    DecisionType,
    ListingType,
    UserRole,
)
from hk_ipo_agent.common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    ProspectusExtraction,
    SingleModelValuation,
    ValuationDistribution,
    ValuationEnsembleOutput,
)
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.prediction_registry.registry import (
    get_registry,
    reset_registry,
)
from hk_ipo_agent.prediction_registry.snapshot import build_snapshot


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Wipe in-memory stores between tests.

    R7-10: also clear the session-factory ContextVar + engine cache so the
    new pytest-asyncio event loop doesn't inherit the previous test's
    asyncpg pool (which is bound to the dead loop and raises
    ``RuntimeError: Future attached to a different loop``).
    """
    from hk_ipo_agent.data.database import async_session_factory

    monkeypatch.setenv("KIMI_API_KEY", "sk-test")
    get_settings.cache_clear()
    async_session_factory.cache_clear()  # type: ignore[attr-defined]
    reset_registry()
    reset_users_for_test()
    reset_audit_store_for_test()
    reset_alert_store_for_test()
    reset_chat_store_for_test()
    reset_event_bus_for_test()
    _seed_defaults()
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Yield a fresh TestClient with the full app mounted.

    R7-10 follow-up: TestClient runs the lifespan synchronously in its own
    ``asgi-lifespan`` loop, which builds an ``async_session_factory()`` /
    engine bound to THAT loop. The actual test then runs in
    pytest-asyncio's per-test loop — different loop → asyncpg pool
    "Future attached to a different loop" error. Fix: after TestClient
    enters (lifespan done), reset both the registry (back to in-memory
    for unit tests that don't touch real PG) and the engine cache so the
    test's own event loop builds a fresh engine.
    """
    from hk_ipo_agent.data.database import async_session_factory
    from hk_ipo_agent.prediction_registry.registry import (
        InMemoryPredictionRegistry,
        set_registry,
    )

    app = create_app()
    with TestClient(app) as c:
        # AFTER lifespan: dispose lifespan's engine/factory + swap to
        # in-memory registry so subsequent async test code runs on its
        # own event loop's engine.
        async_session_factory.cache_clear()  # type: ignore[attr-defined]
        set_registry(InMemoryPredictionRegistry())
        # Lifespan also wires PG-backed EventBus + PGAuditStore bound to
        # the lifespan event loop. Unit tests run on a separate
        # pytest-asyncio loop and don't need PG persistence, so reset to
        # the default in-memory implementations — same rationale as the
        # registry swap above.
        reset_audit_store_for_test()
        reset_event_bus_for_test()
        yield c


@pytest.fixture
def admin_token() -> str:
    """Issue an admin access token (no UI login required)."""
    token, _ = issue_access_token(
        user_id=uuid4(),
        email="admin-test@hk.local",
        roles=[UserRole.ADMIN.value],
    )
    return token


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def _make_test_snapshot():
    """Build + persist a snapshot in the in-memory registry. Returns it."""
    ext = ProspectusExtraction(
        prospectus_id="P-TEST-1",
        company_name_zh="测试公司",
        listing_type=ListingType.MAINBOARD_TECH,
        industry_code="AI",
        industry_description="AI / SaaS",
        business_model="B2B SaaS",
        extraction_version="0.0.1",
        extracted_at=datetime.now(UTC),
    )
    d = ValuationDistribution(
        p10=Decimal("90"),
        p25=Decimal("95"),
        p50=Decimal("100"),
        p75=Decimal("105"),
        p90=Decimal("110"),
        mean=Decimal("100"),
        std=Decimal("5"),
    )
    val = ValuationEnsembleOutput(
        company_id="P-TEST-1",
        single_models=[
            SingleModelValuation(model_name="comparable", applicable=True, valuation_distribution=d)
        ],
        weights_used={"comparable": 1.0},
        ensemble_distribution=d,
        implied_price_range={
            "low": Decimal("95"),
            "fair": Decimal("100"),
            "high": Decimal("105"),
        },
    )
    decision = FinalDecision(
        decision=DecisionType.PARTIAL,
        confidence=0.7,
        suggested_allocation_pct=0.02,
        price_range_low=Decimal("95"),
        price_range_fair=Decimal("100"),
        price_range_high=Decimal("105"),
        expected_return_6m=d,
        expected_return_12m=d,
        scorecard={"overall": 65.0},
    )
    return build_snapshot(
        ipo_id=uuid4(),
        extraction=ext,
        agent_outputs={
            "fundamental": AgentOutput(
                agent_role=AgentRole.FUNDAMENTAL,
                scores={"x": 70.0},
                overall_score=70.0,
                runtime_seconds=0.1,
            ),
        },
        valuation=val,
        debate=DebateOutput(final_consensus="balanced"),
        decision=decision,
        total_cost_usd=Decimal("0.05"),
        runtime_seconds=10.0,
    )


@pytest.fixture
async def seeded_snapshot():
    """Persist a snapshot in the registry and return it."""
    snap = _make_test_snapshot()
    await get_registry().create_snapshot(snap)
    return snap
