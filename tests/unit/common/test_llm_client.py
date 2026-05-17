"""Tests for `hk_ipo_agent.common.llm_client` — KIMI/Moonshot OpenAI-compatible wrapper."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APIStatusError, APITimeoutError, RateLimitError
from pydantic import BaseModel

from hk_ipo_agent.common.exceptions import (
    LLMCostExceededError,
    LLMError,
    LLMOutputValidationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from hk_ipo_agent.common.llm_client import (
    DEFAULT_PRICES_USD_PER_MTOKENS,
    CostLog,
    CostRecord,
    LLMClient,
    LLMResponse,
    _compute_cost,
)

# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------


def test_compute_cost_moonshot_128k_known_prices() -> None:
    # 1M input + 1M output for moonshot-v1-128k = 8.30 + 8.30 = $16.60
    cost = _compute_cost(
        "moonshot-v1-128k",
        tokens_input=1_000_000,
        tokens_output=1_000_000,
        tokens_cache_read=0,
        tokens_cache_write=0,
    )
    assert cost == Decimal("16.60")


def test_compute_cost_moonshot_32k_known_prices() -> None:
    # 1M input + 1M output for moonshot-v1-32k = 3.30 + 3.30 = $6.60
    cost = _compute_cost(
        "moonshot-v1-32k",
        tokens_input=1_000_000,
        tokens_output=1_000_000,
        tokens_cache_read=0,
        tokens_cache_write=0,
    )
    assert cost == Decimal("6.60")


def test_compute_cost_unknown_model_returns_zero() -> None:
    assert _compute_cost("phantom-model", 1000, 1000, 0, 0) == Decimal("0")


def test_default_prices_table_has_required_keys() -> None:
    for model, prices in DEFAULT_PRICES_USD_PER_MTOKENS.items():
        assert {"input", "input_cache_write", "input_cache_read", "output"} == set(prices), (
            f"{model} missing keys"
        )
        for k, v in prices.items():
            assert v >= 0, f"{model}.{k} negative"


# ---------------------------------------------------------------------------
# CostLog
# ---------------------------------------------------------------------------


def test_cost_log_aggregates() -> None:
    log = CostLog()
    log.append(
        CostRecord(
            model="moonshot-v1-128k",
            agent_role="fundamental",
            ipo_id="ipo-1",
            tokens_input=100,
            tokens_output=200,
            tokens_cache_read=0,
            tokens_cache_write=0,
            cost_usd=Decimal("0.05"),
            runtime_seconds=1.2,
            request_id="req-1",
            occurred_at_unix=1.0,
        )
    )
    log.append(
        CostRecord(
            model="moonshot-v1-128k",
            agent_role="synthesizer",
            ipo_id="ipo-1",
            tokens_input=500,
            tokens_output=1000,
            tokens_cache_read=0,
            tokens_cache_write=0,
            cost_usd=Decimal("0.50"),
            runtime_seconds=4.0,
            request_id="req-2",
            occurred_at_unix=2.0,
        )
    )
    assert log.total_usd() == Decimal("0.55")
    assert log.total_for_agent("fundamental") == Decimal("0.05")
    assert log.total_for_agent("synthesizer") == Decimal("0.50")
    assert log.total_for_agent("policy") == Decimal("0")


# ---------------------------------------------------------------------------
# LLMClient async path (mocked)
# ---------------------------------------------------------------------------


def _make_client(monkeypatch: pytest.MonkeyPatch) -> LLMClient:
    """Build a client with a mocked AsyncOpenAI so tests never hit the wire."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-test-12345")
    monkeypatch.setenv("KIMI_URL", "https://api.moonshot.ai/v1")
    return LLMClient(daily_budget_usd=Decimal("1.00"))


def _fake_openai_response(
    *, text: str = "hi", in_tokens: int = 10, out_tokens: int = 20
) -> MagicMock:
    """Build a mock that mimics an OpenAI ChatCompletion response object."""
    response = MagicMock()
    response.id = "chatcmpl-test"
    response.usage = MagicMock(
        prompt_tokens=in_tokens,
        completion_tokens=out_tokens,
    )
    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"
    response.choices = [choice]
    return response


@pytest.mark.asyncio
async def test_acomplete_success_records_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch)
    fake_call: Any = AsyncMock(return_value=_fake_openai_response(text="hello world"))
    client._client.chat.completions.create = fake_call  # type: ignore[attr-defined]

    resp = await client.acomplete(
        model="moonshot-v1-128k",
        messages=[{"role": "user", "content": "ping"}],
        system="You are helpful.",
        agent_role="fundamental",
        ipo_id="ipo-test",
    )
    assert isinstance(resp, LLMResponse)
    assert resp.text == "hello world"
    assert resp.tokens_input == 10
    assert resp.tokens_output == 20
    assert resp.cost_usd > 0
    assert len(client.cost_log.records) == 1
    rec = client.cost_log.records[0]
    assert rec.agent_role == "fundamental"
    assert rec.ipo_id == "ipo-test"


@pytest.mark.asyncio
async def test_acomplete_blocks_when_budget_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If CostLog already exceeds daily budget, refuse to call."""
    client = _make_client(monkeypatch)
    # Manually inflate cost log past budget
    client.cost_log.append(
        CostRecord(
            model="moonshot-v1-128k",
            agent_role=None,
            ipo_id=None,
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=0,
            tokens_cache_write=0,
            cost_usd=Decimal("2.00"),
            runtime_seconds=0,
            request_id=None,
            occurred_at_unix=0,
        )
    )
    fake_call: Any = AsyncMock()
    client._client.chat.completions.create = fake_call  # type: ignore[attr-defined]

    with pytest.raises(LLMCostExceededError):
        await client.acomplete(
            model="moonshot-v1-128k",
            messages=[{"role": "user", "content": "ping"}],
        )
    fake_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_acomplete_system_message_prepended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System prompt is prepended as a system role message in OpenAI format."""
    client = _make_client(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _fake_openai_response()

    client._client.chat.completions.create = fake_create  # type: ignore[attr-defined]

    await client.acomplete(
        model="moonshot-v1-128k",
        messages=[{"role": "user", "content": "x"}],
        system="System guidance.",
    )
    assert "messages" in captured
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "System guidance."
    assert messages[1]["role"] == "user"


# ---------------------------------------------------------------------------
# Retry exhaustion -> typed exceptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acomplete_rate_limit_retries_exhausted_raises_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RateLimitError on every attempt -> LLMRateLimitError after retries exhausted."""
    client = _make_client(monkeypatch)
    client.max_retries = 2  # speed up

    call_count = {"n": 0}

    async def always_rate_limit(**_: Any) -> Any:
        call_count["n"] += 1
        raise RateLimitError("rate limited", response=MagicMock(), body=None)

    client._client.chat.completions.create = always_rate_limit  # type: ignore[attr-defined]

    with pytest.raises(LLMRateLimitError):
        await client.acomplete(
            model="moonshot-v1-128k",
            messages=[{"role": "user", "content": "x"}],
        )
    assert call_count["n"] == 2  # retried up to max_retries


@pytest.mark.asyncio
async def test_acomplete_timeout_retries_exhausted_raises_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """APITimeoutError on every attempt -> LLMTimeoutError after retries exhausted."""
    client = _make_client(monkeypatch)
    client.max_retries = 2

    async def always_timeout(**_: Any) -> Any:
        raise APITimeoutError(request=MagicMock())

    client._client.chat.completions.create = always_timeout  # type: ignore[attr-defined]

    with pytest.raises(LLMTimeoutError):
        await client.acomplete(
            model="moonshot-v1-128k",
            messages=[{"role": "user", "content": "x"}],
        )


@pytest.mark.asyncio
async def test_acomplete_non_429_api_status_error_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4xx other than 429 should NOT be retried; should raise LLMError immediately."""
    client = _make_client(monkeypatch)
    call_count = {"n": 0}

    async def server_error(**_: Any) -> Any:
        call_count["n"] += 1
        err = APIStatusError(
            "bad request",
            response=MagicMock(status_code=400),
            body={"error": {"message": "bad"}},
        )
        err.status_code = 400  # type: ignore[attr-defined]
        err.message = "bad"  # type: ignore[attr-defined]
        raise err

    client._client.chat.completions.create = server_error  # type: ignore[attr-defined]

    with pytest.raises(LLMError):
        await client.acomplete(
            model="moonshot-v1-128k",
            messages=[{"role": "user", "content": "x"}],
        )
    assert call_count["n"] == 1  # not retried


# ---------------------------------------------------------------------------
# Structured JSON output (acomplete_json)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acomplete_json_parses_valid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acomplete_json returns a parsed Pydantic instance on valid JSON."""
    client = _make_client(monkeypatch)

    class _Stub(BaseModel):
        value: int
        name: str

    payload = '{"value": 42, "name": "ok"}'
    client._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
        return_value=_fake_openai_response(text=payload)
    )

    result = await client.acomplete_json(
        model="moonshot-v1-128k",
        messages=[{"role": "user", "content": "give me json"}],
        response_model=_Stub,
    )
    assert isinstance(result, _Stub)
    assert result.value == 42
    assert result.name == "ok"


@pytest.mark.asyncio
async def test_acomplete_json_strips_code_fences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acomplete_json should extract JSON from ```json ... ``` code blocks."""
    client = _make_client(monkeypatch)

    class _Stub(BaseModel):
        x: int

    fenced = 'Here is the JSON:\n```json\n{"x": 99}\n```'
    client._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
        return_value=_fake_openai_response(text=fenced)
    )

    result = await client.acomplete_json(
        model="moonshot-v1-128k",
        messages=[{"role": "user", "content": "x"}],
        response_model=_Stub,
    )
    assert result.x == 99


@pytest.mark.asyncio
async def test_acomplete_json_retries_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First response invalid -> retry with feedback -> succeed."""
    client = _make_client(monkeypatch)

    class _Stub(BaseModel):
        value: int

    responses = iter(
        [
            _fake_openai_response(text="not json at all"),
            _fake_openai_response(text='{"value": 7}'),
        ]
    )

    async def fake_create(**_: Any) -> Any:
        return next(responses)

    client._client.chat.completions.create = fake_create  # type: ignore[attr-defined]

    result = await client.acomplete_json(
        model="moonshot-v1-128k",
        messages=[{"role": "user", "content": "x"}],
        response_model=_Stub,
        max_retries=2,
    )
    assert result.value == 7


@pytest.mark.asyncio
async def test_acomplete_json_raises_after_all_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(monkeypatch)

    class _Stub(BaseModel):
        value: int

    client._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
        return_value=_fake_openai_response(text="still not json")
    )

    with pytest.raises(LLMOutputValidationError):
        await client.acomplete_json(
            model="moonshot-v1-128k",
            messages=[{"role": "user", "content": "x"}],
            response_model=_Stub,
            max_retries=1,
        )
