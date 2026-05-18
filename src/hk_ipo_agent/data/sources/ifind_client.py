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

Indicator IDs + endpoint conventions inherited from the DCF agent's
``shared/ifind_client.py`` (production-verified for HK IPO use). See
``data/knowledge_base/ifind_indicator_catalog.csv`` for the verified
indicator catalog and ADR 0008 for the dual-track integration.

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

from pydantic import SecretStr
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
# Common indicator strings (DCF agent verified) — see catalog CSV for full list
# ---------------------------------------------------------------------------

# HK IPO calendar columns (data_pool 'newshare')
HK_IPO_CALENDAR_INDICATORS: str = ";".join(
    [
        "thscode",
        "ths_stock_short_name_stock",
        "ths_ipo_date_stock",
        "ths_ipo_price_hks",
        "ths_ipo_pe_hks",
        "ths_total_share_after_ipo_hks",
        "ths_ipo_amt_hks",
    ]
)

# HK IPO basics (per-ticker snapshot)
HK_IPO_BASICS_INDICATORS: str = ";".join(
    [
        "ths_stock_short_name_stock",
        "ths_ipo_date_stock",
        "ths_ipo_price_hks",
        "ths_ipo_pe_hks",
        "ths_subscript_times_hks",
        "ths_intl_subscript_times_hks",
        "ths_ipo_amt_hks",
        "ths_listing_recommend_hks",
        "ths_underwriter_hks",
        "ths_first_day_close_chg_hks",
    ]
)

# Quarterly / period-end financials (single period snapshot)
FINANCIAL_INDICATORS_SINGLE_PERIOD: str = ";".join(
    [
        "ths_oper_total_rev",
        "ths_oper_rev",
        "ths_oper_cost",
        "ths_op_profit",
        "ths_total_profit",
        "ths_net_profit",
        "ths_np_atoopc",
        "ths_total_assets",
        "ths_total_liab",
        "ths_total_se",
        "ths_se_atoopc",
        "ths_cash_eqv_end_period",
        "ths_oper_cash_flow",
        "ths_invest_cash_flow",
        "ths_finan_cash_flow",
        "ths_capex",
    ]
)

# Valuation snapshot (universal: HK / A / US)
VALUATION_SNAPSHOT_INDICATORS: str = ";".join(
    [
        "pe_ttm",
        "ps_ttm",
        "pb_latest",
        "ev1_to_ebitda",
        "roe_ttm",
        "market_value",
        "total_shares",
    ]
)

# HK macro index tickers (via history_quotes endpoint)
HK_MACRO_QUOTE: dict[str, str] = {
    "HSI": "HSI.HI",
    "HSCEI": "HSCEI.HI",
    "HSTECH": "HSTECH.HI",
    "USDHKD": "USDHKD.FX",
    "USDCNH": "USDCNH.FX",
}


# ---------------------------------------------------------------------------
# Lazy iFinDPy loader (NOT imported at module-load time)
# ---------------------------------------------------------------------------


def _load_ifindpy() -> Any:
    """Import iFinDPy on demand. Raises MissingDependencyError if absent."""
    try:
        import iFinDPy
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
        password: str | SecretStr | None = None,
        qps_limit: int | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        sources_cfg = load_data_sources_config().get("ifind", {})
        self.username = username or settings.ifind.username

        # R7-5: store password as ``SecretStr`` internally (private attribute).
        # Pre-fix the cleartext lived on ``self.password`` for the lifetime of
        # the client; pickle dumps / __repr__ / locals-in-stacktrace all leaked
        # it. Now the only place cleartext exists is inside
        # ``self._password.get_secret_value()`` calls at the SDK boundary.
        if isinstance(password, SecretStr):
            self._password: SecretStr = password
        elif password is not None:
            self._password = SecretStr(password)
        else:
            # settings.ifind.password is already SecretStr; pass through.
            self._password = settings.ifind.password

        self.qps_limit = qps_limit or int(sources_cfg.get("qps_limit", settings.ifind.qps_limit))
        self.timeout_seconds = timeout_seconds or int(sources_cfg.get("timeout_seconds", 30))
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
        # R7-5: extract cleartext only at the SDK call boundary; the SecretStr
        # never leaves this stack frame.
        result = await asyncio.to_thread(
            self._sdk.THS_iFinDLogin, self.username, self._password.get_secret_value()
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
        report_date: date,
        consolidate: str = "OC",
        indicators: str | None = None,
    ) -> Any:
        """Period-end financial snapshot for one ticker.

        Args:
            ticker:        e.g. ``"2228.HK"`` or ``"600519.SH"``
            as_of_date:    look-ahead guard; refuses calls where as_of_date < report_date
            report_date:   the fiscal-period end (e.g. ``date(2024,12,31)`` for FY24)
            consolidate:   ``"OC"`` consolidated / ``"PC"`` parent-company only
            indicators:    semicolon-joined indicator IDs;
                           defaults to ``FINANCIAL_INDICATORS_SINGLE_PERIOD``
        """
        self._require_as_of(as_of_date)
        if report_date > as_of_date:
            raise LookAheadError(
                f"report_date {report_date} > as_of_date {as_of_date}",
                ticker=ticker,
            )
        inds = indicators or FINANCIAL_INDICATORS_SINGLE_PERIOD
        params = f"{report_date.strftime('%Y%m%d')},100,{consolidate}"
        return await self._call(
            IFindRequest(
                method="THS_BasicData",
                args=(ticker, inds),
                kwargs={"params": params},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_BasicData(ticker, inds, params),
        )

    async def get_ipo_history(
        self,
        as_of_date: date,
        *,
        start: date,
        pool_filter: str = "AHK",
    ) -> Any:
        """HK IPO calendar via ``data_pool('newshare', ...)`` (DCF agent verified).

        Args:
            as_of_date:  look-ahead guard
            start:       window start (inclusive)
            pool_filter: ``"AHK"`` = all HK new shares; see iFind data browser
                         for sub-board filters (mainboard / GEM / etc.)
        """
        self._require_as_of(as_of_date)
        if start > as_of_date:
            raise ValueError("start must be <= as_of_date")
        params = f"{pool_filter};{start.isoformat()};{as_of_date.isoformat()}"
        return await self._call(
            IFindRequest(
                method="THS_DataPool",
                args=("newshare", params, HK_IPO_CALENDAR_INDICATORS),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_DataPool("newshare", params, HK_IPO_CALENDAR_INDICATORS),
        )

    async def get_ipo_basics(
        self,
        tickers: str | list[str],
        as_of_date: date,
    ) -> Any:
        """Per-ticker IPO snapshot (sponsor, underwriter, subscription multiples).

        DCF agent ``hk_ipo_basics`` equivalent.
        """
        self._require_as_of(as_of_date)
        joined = ",".join(tickers) if isinstance(tickers, list) else tickers
        return await self._call(
            IFindRequest(
                method="THS_BasicData",
                args=(joined, HK_IPO_BASICS_INDICATORS),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_BasicData(joined, HK_IPO_BASICS_INDICATORS, ""),
        )

    async def get_hk_history_prices(
        self,
        tickers: str | list[str],
        as_of_date: date,
        *,
        start: date,
        adjust: str = "F",
        currency: str = "HKD",
    ) -> Any:
        """HK daily OHLCV history up to ``as_of_date``.

        Args:
            adjust:   ``"N"`` raw / ``"F"`` forward-adjusted / ``"B"`` backward-adjusted
            currency: ``"HKD"`` / ``"CNY"`` / ``"USD"``
        """
        self._require_as_of(as_of_date)
        if start > as_of_date:
            raise ValueError("start must be <= as_of_date")
        cps_map = {"N": "00100", "F": "00102", "B": "00103"}
        cps = cps_map[adjust]
        options = f"Interval:D,CPS:{cps},baseDate:1900-01-01,Currency:{currency}"
        indicators = "open;high;low;close;volume;amount;turnoverRatio;changeRatio"
        joined = ",".join(tickers) if isinstance(tickers, list) else tickers
        return await self._call(
            IFindRequest(
                method="THS_HistoryQuotes",
                args=(joined, indicators, options, start, as_of_date),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_HistoryQuotes(
                joined,
                indicators,
                options,
                start.isoformat(),
                as_of_date.isoformat(),
            ),
        )

    async def get_comparable_companies(
        self,
        industry_code: str,
        as_of_date: date,
        *,
        market: str = "HK",
    ) -> Any:
        """List peers within ``industry_code`` listed before ``as_of_date``.

        Phase 4 valuation/comparable.py consumer.
        """
        self._require_as_of(as_of_date)
        params = f"code:{industry_code};exchange:{market};date:{as_of_date.isoformat()}"
        return await self._call(
            IFindRequest(
                method="THS_DataPool",
                args=("industry", params),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_DataPool("industry", params, "thscode;name"),
        )

    async def get_valuation_snapshot(
        self,
        tickers: str | list[str],
        as_of_date: date,
    ) -> Any:
        """PE/PS/PB/EV-EBITDA/market cap snapshot. Universal across HK/A/US."""
        self._require_as_of(as_of_date)
        joined = ",".join(tickers) if isinstance(tickers, list) else tickers
        params = f"{as_of_date.isoformat()},100"
        return await self._call(
            IFindRequest(
                method="THS_BasicData",
                args=(joined, VALUATION_SNAPSHOT_INDICATORS, params),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_BasicData(joined, VALUATION_SNAPSHOT_INDICATORS, params),
        )

    async def get_ah_premium_history(
        self,
        ticker_pair: tuple[str, str],
        as_of_date: date,
        *,
        lookback_days: int = 365,
    ) -> Any:
        """Historical A/H premium timeseries up to ``as_of_date``.

        Returns paired daily closes for the H + A tickers; caller computes
        the premium ratio. Phase 4 valuation/ah_premium.py consumer.
        """
        self._require_as_of(as_of_date)
        from datetime import timedelta

        h_ticker, a_ticker = ticker_pair
        start = as_of_date - timedelta(days=lookback_days)
        joined = f"{h_ticker},{a_ticker}"
        # Closes only; agent computes the premium itself.
        options = "Interval:D,CPS:00102,baseDate:1900-01-01,Currency:original"
        return await self._call(
            IFindRequest(
                method="THS_HistoryQuotes",
                args=(joined, "close", options, start, as_of_date),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_HistoryQuotes(
                joined, "close", options, start.isoformat(), as_of_date.isoformat()
            ),
        )

    async def get_macro_index_history(
        self,
        as_of_date: date,
        *,
        start: date,
        index_keys: list[str] | None = None,
    ) -> Any:
        """HSI / HSCEI / HSTECH / FX history. ``index_keys`` are keys of ``HK_MACRO_QUOTE``.

        Used by Phase 7.5 benchmarks.py + Phase 8 regime_detection.py.
        """
        self._require_as_of(as_of_date)
        if start > as_of_date:
            raise ValueError("start must be <= as_of_date")
        keys = index_keys or list(HK_MACRO_QUOTE.keys())
        tickers = ",".join(HK_MACRO_QUOTE[k] for k in keys if k in HK_MACRO_QUOTE)
        options = "Interval:D,Currency:original"
        return await self._call(
            IFindRequest(
                method="THS_HistoryQuotes",
                args=(tickers, "close;volume;changeRatio", options, start, as_of_date),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_HistoryQuotes(
                tickers,
                "close;volume;changeRatio",
                options,
                start.isoformat(),
                as_of_date.isoformat(),
            ),
        )

    async def query_edb(
        self,
        indicator_ids: str | list[str],
        as_of_date: date,
        *,
        start: date,
    ) -> Any:
        """Macro EDB time series. ``indicator_ids`` joined with ``;``."""
        self._require_as_of(as_of_date)
        joined = ";".join(indicator_ids) if isinstance(indicator_ids, list) else indicator_ids
        return await self._call(
            IFindRequest(
                method="THS_EDBQuery",
                args=(joined, start, as_of_date),
                kwargs={},
                as_of_date=as_of_date,
            ),
            lambda sdk: sdk.THS_EDBQuery(joined, start.isoformat(), as_of_date.isoformat()),
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
        """Apply rate-limit + retry + timeout around one SDK call.

        R7-6: exceptions are routed through ``_classify_exception_for_retry``
        so network-jitter (ConnectionError / TimeoutError) becomes a
        retryable ``DataSourceUnavailableError`` while logic errors stay
        as a non-retryable ``DataSourceError``. Pre-fix every non-timeout
        exception was lumped into the non-retryable bucket — a single
        ``ConnectionResetError`` would surface as a hard failure on attempt 1
        instead of recovering on attempt 2.
        """
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
                except Exception as exc:
                    log.exception("ifind_call_failed", method=request.method)
                    raise _classify_exception_for_retry(
                        exc, method=request.method, timeout_seconds=self.timeout_seconds
                    ) from exc
        raise DataSourceError("iFind retry loop exited without result")  # pragma: no cover


# R7-6: exceptions that indicate a transient network problem and should be
# retried with exponential backoff. Logic errors (ValueError / KeyError /
# generic RuntimeError) fall through to the non-retryable DataSourceError
# bucket because no amount of retrying fixes a malformed query.
_NETWORK_JITTER_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,  # covers ConnectionResetError / Aborted / Refused
    TimeoutError,
)


def _classify_exception_for_retry(
    exc: BaseException, *, method: str, timeout_seconds: int | None = None
) -> DataSourceError:
    """R7-6: classify a raw exception into retryable vs non-retryable.

    Returns the WRAPPED exception (``DataSourceUnavailableError`` or
    ``DataSourceError``). Caller is responsible for chaining via
    ``raise ... from exc``.

    The split is binary:
      * Network jitter (``ConnectionError`` / ``TimeoutError`` and subclasses)
        → ``DataSourceUnavailableError`` (retryable). The tenacity retry
        predicate matches this exact class so the retry loop kicks in.
      * Everything else → ``DataSourceError`` (non-retryable). Includes
        ``ValueError`` / ``TypeError`` / ``KeyError`` (caller bugs),
        ``RuntimeError`` (SDK protocol violations), and any exception we
        haven't seen yet — better to fail fast than to retry a hopeless
        call and burn the QPS budget.
    """
    if isinstance(exc, TimeoutError) and timeout_seconds is not None:
        msg = f"iFind {method} timed out after {timeout_seconds}s"
    elif isinstance(exc, _NETWORK_JITTER_EXCEPTIONS):
        msg = f"iFind {method} network jitter: {exc}"
    else:
        msg = f"iFind {method} failed: {exc}"

    if isinstance(exc, _NETWORK_JITTER_EXCEPTIONS):
        return DataSourceUnavailableError(msg, method=method)
    return DataSourceError(msg, method=method)


__all__ = ("IFindClient", "IFindRequest")
