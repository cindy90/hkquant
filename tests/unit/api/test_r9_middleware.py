"""R9-2 — direct tests for RateLimit / CostGuard / CORS-prod middlewares.

Pre-R9 the middleware layer had only one test file (test_r6_cost_guard_exact_path.py)
covering R6-5's segment-boundary path matcher. The rest of the
middleware behaviour (rate-limit 429, CORS prod-mode rejection of '*',
CostGuard 503 when budget exhausted) was uncovered.

These tests poke each middleware via lightweight stand-ins (no real
FastAPI app for the unit-level helpers; the integration smoke uses
TestClient + the full app).
"""

# R9-2: stub classes use ``= [...]``  default-value pattern and lambdas
# that wrap them — both stylistically idiomatic for monkeypatch fixtures.
# ruff: noqa: PLW0108, RUF012

from __future__ import annotations

import pytest

from hk_ipo_agent.api.middleware.cors import install_cors

# ---------------------------------------------------------------------- CORS prod guard


def test_cors_install_rejects_wildcard_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — install_cors raises RuntimeError when allow_origins contains '*'
    AND environment=prod. CLAUDE.md v1.2.1 baseline.
    """
    from fastapi import FastAPI

    from hk_ipo_agent.common.settings import get_settings

    # Monkeypatch get_settings to return a stub with prod environment + '*' origin.
    class _StubAPI:
        cors_origins = ["*"]
        rate_limit_per_min = 60

    class _StubSettings:
        environment = "prod"
        api = _StubAPI()

    monkeypatch.setattr("hk_ipo_agent.api.middleware.cors.get_settings", lambda: _StubSettings())
    _ = get_settings  # silence unused-import

    app = FastAPI()
    with pytest.raises(RuntimeError, match=r"CORS origin .* not allowed in production"):
        install_cors(app)


def test_cors_install_accepts_wildcard_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — dev env tolerates '*' origin (developer convenience)."""
    from fastapi import FastAPI

    class _StubAPI:
        cors_origins = ["*"]
        rate_limit_per_min = 60

    class _StubSettings:
        environment = "dev"
        api = _StubAPI()

    monkeypatch.setattr("hk_ipo_agent.api.middleware.cors.get_settings", lambda: _StubSettings())
    app = FastAPI()
    install_cors(app)  # no raise


def test_cors_install_accepts_concrete_origins_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — prod with a concrete whitelist → no raise."""
    from fastapi import FastAPI

    class _StubAPI:
        cors_origins = ["https://hkipo.example.com", "https://admin.example.com"]
        rate_limit_per_min = 60

    class _StubSettings:
        environment = "prod"
        api = _StubAPI()

    monkeypatch.setattr("hk_ipo_agent.api.middleware.cors.get_settings", lambda: _StubSettings())
    app = FastAPI()
    install_cors(app)


# ---------------------------------------------------------------------- RateLimit


@pytest.mark.asyncio
async def test_rate_limit_allows_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — under the limit → request passes through, 200."""
    from unittest.mock import AsyncMock, MagicMock

    from hk_ipo_agent.api.middleware.rate_limit import RateLimitMiddleware

    class _StubAPI:
        rate_limit_per_min = 5

    class _StubSettings:
        api = _StubAPI()

    monkeypatch.setattr(
        "hk_ipo_agent.api.middleware.rate_limit.get_settings", lambda: _StubSettings()
    )

    mw = RateLimitMiddleware(app=MagicMock())
    request = MagicMock()
    request.state = MagicMock(spec=[])  # no current_user attribute
    request.client = MagicMock()
    request.client.host = "10.0.0.1"

    expected = MagicMock(status_code=200)
    call_next = AsyncMock(return_value=expected)
    resp = await mw.dispatch(request, call_next)
    assert resp is expected
    call_next.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_rate_limit_returns_429_when_bucket_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — past the limit on the same key → 429 short-circuit."""
    from unittest.mock import AsyncMock, MagicMock

    from hk_ipo_agent.api.middleware.rate_limit import RateLimitMiddleware

    class _StubAPI:
        rate_limit_per_min = 2  # tiny limit so test is cheap

    class _StubSettings:
        api = _StubAPI()

    monkeypatch.setattr(
        "hk_ipo_agent.api.middleware.rate_limit.get_settings", lambda: _StubSettings()
    )

    mw = RateLimitMiddleware(app=MagicMock())

    def _req() -> object:
        r = MagicMock()
        r.state = MagicMock(spec=[])
        r.client = MagicMock()
        r.client.host = "10.0.0.99"
        return r

    call_next = AsyncMock(return_value=MagicMock(status_code=200))
    # Two requests succeed
    await mw.dispatch(_req(), call_next)
    await mw.dispatch(_req(), call_next)
    # Third 429s
    resp = await mw.dispatch(_req(), call_next)
    assert resp.status_code == 429
    # call_next NOT called for the rejected request.
    assert call_next.await_count == 2


@pytest.mark.asyncio
async def test_rate_limit_disabled_when_limit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — limit<=0 → middleware is a passthrough."""
    from unittest.mock import AsyncMock, MagicMock

    from hk_ipo_agent.api.middleware.rate_limit import RateLimitMiddleware

    class _StubAPI:
        rate_limit_per_min = 0  # disabled

    class _StubSettings:
        api = _StubAPI()

    monkeypatch.setattr(
        "hk_ipo_agent.api.middleware.rate_limit.get_settings", lambda: _StubSettings()
    )

    mw = RateLimitMiddleware(app=MagicMock())
    request = MagicMock()
    request.state = MagicMock(spec=[])
    request.client = MagicMock()
    request.client.host = "10.0.0.42"

    expected = MagicMock(status_code=200)
    call_next = AsyncMock(return_value=expected)
    # 10 requests all pass with no limit.
    for _ in range(10):
        resp = await mw.dispatch(request, call_next)
        assert resp is expected


# ---------------------------------------------------------------------- CostGuard


@pytest.mark.asyncio
async def test_cost_guard_returns_503_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-2 — total LLM spend ≥ budget → 503 on non-cheap paths."""
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from hk_ipo_agent.api.middleware.cost_guard import CostGuardMiddleware

    class _StubLLM:
        class _CostLog:
            def total_usd(self) -> float:
                return 999.99  # > budget

        cost_log = _CostLog()

    class _StubLLMSettings:
        cost_daily_budget_usd = Decimal("100")

    class _StubSettings:
        llm = _StubLLMSettings()

    monkeypatch.setattr(
        "hk_ipo_agent.api.middleware.cost_guard.get_settings", lambda: _StubSettings()
    )

    mw = CostGuardMiddleware(app=MagicMock())
    request = MagicMock()
    request.url = MagicMock()
    request.url.path = "/api/analysis/run"  # non-cheap path
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.app.state.llm_client = _StubLLM()

    call_next = AsyncMock(return_value=MagicMock(status_code=200))
    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 503
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_cost_guard_passes_when_under_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-2 — total LLM spend < budget → request proceeds."""
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from hk_ipo_agent.api.middleware.cost_guard import CostGuardMiddleware

    class _StubLLM:
        class _CostLog:
            def total_usd(self) -> float:
                return 0.50

        cost_log = _CostLog()

    class _StubLLMSettings:
        cost_daily_budget_usd = Decimal("100")

    class _StubSettings:
        llm = _StubLLMSettings()

    monkeypatch.setattr(
        "hk_ipo_agent.api.middleware.cost_guard.get_settings", lambda: _StubSettings()
    )

    mw = CostGuardMiddleware(app=MagicMock())
    request = MagicMock()
    request.url = MagicMock()
    request.url.path = "/api/analysis/run"
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.app.state.llm_client = _StubLLM()

    expected = MagicMock(status_code=200)
    call_next = AsyncMock(return_value=expected)
    resp = await mw.dispatch(request, call_next)
    assert resp is expected
    call_next.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_cost_guard_skips_check_when_llm_client_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-2 — when app.state.llm_client is None (dev fallback), middleware
    passes through unconditionally — matches the R6-6 dev-mode behaviour."""
    from unittest.mock import AsyncMock, MagicMock

    from hk_ipo_agent.api.middleware.cost_guard import CostGuardMiddleware

    mw = CostGuardMiddleware(app=MagicMock())
    request = MagicMock()
    request.url = MagicMock()
    request.url.path = "/api/analysis/run"
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.app.state.llm_client = None

    expected = MagicMock(status_code=200)
    call_next = AsyncMock(return_value=expected)
    resp = await mw.dispatch(request, call_next)
    assert resp is expected
