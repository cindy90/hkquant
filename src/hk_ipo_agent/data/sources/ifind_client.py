"""iFind Python SDK wrapper per PROJECT_SPEC.md §3.4.

iFinDPy (同花顺 QuantAPI) is NOT on PyPI — it ships with the Tonghuashun
desktop client. This module is therefore designed to:

1. Import iFinDPy lazily so the rest of the project (and CI) can import
   the iFindClient class without iFinDPy installed.
2. Raise :class:`hk_ipo_agent.common.exceptions.MissingDependencyError`
   with actionable_info when called without iFinDPy.
3. Enforce ``as_of_date`` on every accessor so look-ahead leaks are
   impossible (ADR 0005 §4 invariant; see also tests/unit/data/test_no_lookahead.py).
4. Apply tenacity retry + per-call timeout + a token-bucket QPS limiter
   sourced from ``config/data_sources.yaml``.

Tests should never hit the real SDK — they mock ``IFindClient`` methods
directly (see ``tests/conftest.py`` for the pattern used with LLMClient).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from typing import Any

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...common.exceptions import (
    DataSourceError,
    DataSourceUnavailableError,
    LookAheadError,
    MissingDependencyError,
)
from ...common.logging import get_logger
from ...common.settings import get_settings, load_data_sources_config

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lazy iFinDPy loader (NOT imported at module-load time)
# ---------------------------------------------------------------------------


def _load_ifindpy() -> Any:
    """Import iFinDPy on demand. Raises MissingDependencyError if absent."""
    try:
        import iFinDPy  # type: ignore[import-not-found]  # noqa: PLC0415 — intentional lazy import
    except ImportError as exc:
        raise MissingDependencyError(
            "iFinDPy is not installed. It ships with the Tonghuashun "
            "QuantAPI client; install per src/data_sources/ifind/README.md.",
            install_hint="See https://docs.hithink.com/ for the client install + register flow.",
        ) from exc
    return iFinDPy


# ---------------------------------------------------------------------------
# Token-bucket rate limiter (process-local)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Simple async token bucket. Caller awaits acquire() before each call."""

    def __init__(self, qps_limit: int) -> None:
        if qps_limit < 1:
            raise ValueError("qps_limit must be >= 1")
        self._interval = 1.0 / qps_limit
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_allowed - now)
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = max(now, self._next_allowed) + self._interval


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class IFindRequest:
    """Captured request metadata for logging / debugging."""

    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    as_of_date: date


class IFindClient:
    """Async iFind SDK wrapper. Every accessor takes ``as_of_date`` (required).

    Lifecycle:
        client = IFindClient()
        await client.connect()
        df = await client.get_financials("2228.HK", date(2024, 6, 1), fields=["revenue"])
        await client.disconnect()
    """

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        qps_limit: int | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        sources_cfg = load_data_sources_config().get("ifind", {})
        self.username = username or settings.ifind.username
        self.password = (
            password
            if password is not None
            else settings.ifind.password.get_secret_value()
        )
        self.qps_limit = qps_limit or int(sources_cfg.get("qps_limit", settings.ifind.qps_limit))
        self.timeout_seconds = timeout_seconds or int(
            sources_cfg.get("timeout_seconds", 30)
        )
        self.max_retries = max_retries or int(sources_cfg.get("retry_max", 3))
        self._rate_limiter = _RateLimiter(self.qps_limit)
        self._sdk: Any | None = None
        self._connected = False

    # ------------------------------------------------------------------ lifecycle

    async def connect(self) -> None:
        """Lazy-load iFinDPy and log in. No-op if already connected."""
        if self._connected:
            return
        self._sdk = _load_ifindpy()
        # iFinDPy.THS_iFinDLogin is blocking; offload to thread.
        result = await asyncio.to_thread(
            self._sdk.THS_iFinDLogin, self.username, self.password
        )
        if result != 0:
            raise DataSourceUnavailableError(
                f"iFind login failed with code {result}",
                actionable_info=(
                    "Check IFIND_USERNAME / IFIND_PASSWORD env vars and that "
                    "the Tonghuashun client is running locally."
                ),
            )
        self._connected = True
        log.info("ifind_login_ok", user=self.username)

    async def disconnect(self) -> None:
        if not self._connected or self._sdk is None:
            return
        with suppress(Exception):
            await asyncio.to_thread(self._sdk.THS_iFinDLogout)
        self._connected = False
        self._sdk = None

    async def __aenter__(self) -> IFindClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------ accessors

    async def get_financials(
        self,
        ticker: str,
        as_of_date: date,
        *,
        fields: list[str],
        years: int = 5,
    ) -> Any:
        """Pull annual financials, masking any period ending >= as_of_date.

        Returns the raw iFind result (typically a pandas-like object). The
        repository layer (Phase 2 builders) normalizes into FinancialSnapshotRow.
        """
        self._require_as_of(as_of_date)
        return await self._call(
            IFindRequest(
                method="THS_BasicData",
                args=(ticker, ",".join(fields)),
                kwargs={"years": years},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_BasicData(ticker, ",".join(fields), years),
        )

    async def get_ipo_history(
        self,
        as_of_date: date,
        *,
        market: str = "HK",
        start: date,
    ) -> Any:
        """List IPOs listed in [start, as_of_date]. Future IPOs are masked out."""
        self._require_as_of(as_of_date)
        if start > as_of_date:
            raise ValueError("start must be <= as_of_date")
        return await self._call(
            IFindRequest(
                method="THS_iFindDataQuery_IPOHistory",
                args=(market, start, as_of_date),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_DataPool(
                "ipo",
                f"date:{start.isoformat()},{as_of_date.isoformat()};exchange:{market}",
            ),
        )

    async def get_comparable_companies(
        self,
        industry_code: str,
        as_of_date: date,
        *,
        market: str = "HK",
    ) -> Any:
        """List peers within ``industry_code`` listed before ``as_of_date``."""
        self._require_as_of(as_of_date)
        return await self._call(
            IFindRequest(
                method="THS_iFindDataQuery_Industry",
                args=(industry_code, market, as_of_date),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_DataPool(
                "industry",
                f"code:{industry_code};exchange:{market};date:{as_of_date.isoformat()}",
            ),
        )

    async def get_ah_premium_history(
        self,
        ticker_pair: tuple[str, str],
        as_of_date: date,
        *,
        lookback_days: int = 365,
    ) -> Any:
        """Historical A/H premium timeseries up to (and including) ``as_of_date``."""
        self._require_as_of(as_of_date)
        h_ticker, a_ticker = ticker_pair
        return await self._call(
            IFindRequest(
                method="THS_iFindDataQuery_AHPremium",
                args=(h_ticker, a_ticker, as_of_date, lookback_days),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_HistoryQuotes(
                f"{h_ticker},{a_ticker}",
                "ths_close_price_stock",
                f"DateFormat:0,Tradeday:0,Currtype:HK_HKD;StartDate:{as_of_date.toordinal() - lookback_days};EndDate:{as_of_date.isoformat()}",
            ),
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _require_as_of(as_of_date: date | None) -> None:
        """Refuse any access without an explicit as_of_date."""
        if as_of_date is None:
            raise LookAheadError(
                "as_of_date is required for all iFind queries (ADR 0005 §4 invariant)"
            )

    async def _call(
        self,
        request: IFindRequest,
        runner: Callable[[Any], Any],
    ) -> Any:
        """Apply rate-limit + retry + timeout around one SDK call."""
        if not self._connected:
            await self.connect()
        assert self._sdk is not None

        retry = AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception_type(DataSourceUnavailableError),
            reraise=True,
        )

        async for attempt in retry:
            with attempt:
                await self._rate_limiter.acquire()
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(runner, self._sdk),
                        timeout=self.timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise DataSourceUnavailableError(
                        f"iFind {request.method} timed out after {self.timeout_seconds}s",
                        method=request.method,
                    ) from exc
                except Exception as exc:
                    log.exception("ifind_call_failed", method=request.method)
                    raise DataSourceError(
                        f"iFind {request.method} failed: {exc}",
                        method=request.method,
                    ) from exc
        raise DataSourceError("iFind retry loop exited without result")  # pragma: no cover


__all__ = ("IFindClient", "IFindRequest")
