"""Unified async LLM client per PROJECT_SPEC.md §3.3.

Wraps the OpenAI-compatible AsyncClient (for KIMI/Moonshot API) and adds:
- Exponential-backoff retry (max 3 attempts; honors Retry-After when present)
- Per-call timeout (default 120s)
- Token + USD cost tracking, persisted to a CostLog
- structlog context (agent_role, ipo_id, model)

KIMI API is OpenAI-compatible: https://api.moonshot.ai/v1
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .exceptions import (
    LLMCostExceededError,
    LLMError,
    LLMOutputValidationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from .logging import get_logger
from .settings import get_settings

T = TypeVar("T", bound=BaseModel)

_log = get_logger(__name__)

# Default per-1M-token USD prices for KIMI/Moonshot models.
# Updated based on Moonshot AI pricing (2024-2026).
# moonshot-v1-128k: input ¥60/M tokens, output ¥60/M tokens (~$8.3 USD/M)
DEFAULT_PRICES_USD_PER_MTOKENS: dict[str, dict[str, Decimal]] = {
    "moonshot-v1-128k": {
        "input": Decimal("8.30"),
        "input_cache_write": Decimal("0"),
        "input_cache_read": Decimal("0"),
        "output": Decimal("8.30"),
    },
    "moonshot-v1-32k": {
        "input": Decimal("3.30"),
        "input_cache_write": Decimal("0"),
        "input_cache_read": Decimal("0"),
        "output": Decimal("3.30"),
    },
    "moonshot-v1-8k": {
        "input": Decimal("1.70"),
        "input_cache_write": Decimal("0"),
        "input_cache_read": Decimal("0"),
        "output": Decimal("1.70"),
    },
    "kimi-k2.6": {
        "input": Decimal("10.00"),
        "input_cache_write": Decimal("0"),
        "input_cache_read": Decimal("0"),
        "output": Decimal("10.00"),
    },
}


@dataclass
class CostRecord:
    """Single LLM call cost record."""

    model: str
    agent_role: str | None
    ipo_id: str | None
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_write: int
    cost_usd: Decimal
    runtime_seconds: float
    request_id: str | None
    occurred_at_unix: float


@dataclass
class CostLog:
    """In-memory cost log. Plug a real persistence layer in Phase 7 (audit_logs)."""

    records: list[CostRecord] = field(default_factory=list)

    def append(self, record: CostRecord) -> None:
        self.records.append(record)

    def total_usd(self) -> Decimal:
        return sum((r.cost_usd for r in self.records), Decimal("0"))

    def total_for_agent(self, agent_role: str) -> Decimal:
        return sum((r.cost_usd for r in self.records if r.agent_role == agent_role), Decimal("0"))


@dataclass
class LLMResponse:
    """Normalized LLM response payload."""

    text: str
    model: str
    stop_reason: str | None
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_write: int
    cost_usd: Decimal
    runtime_seconds: float
    request_id: str | None
    raw: Any


def _compute_cost(
    model: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int,
    tokens_cache_write: int,
) -> Decimal:
    """Compute USD cost using DEFAULT_PRICES_USD_PER_MTOKENS for known models."""
    price = DEFAULT_PRICES_USD_PER_MTOKENS.get(model)
    if price is None:
        # Unknown model — return zero rather than fail; log a warning.
        _log.warning("llm_cost_unknown_model", model=model)
        return Decimal("0")
    million = Decimal("1000000")
    return (
        Decimal(tokens_input) * price["input"] / million
        + Decimal(tokens_output) * price["output"] / million
        + Decimal(tokens_cache_read) * price["input_cache_read"] / million
        + Decimal(tokens_cache_write) * price["input_cache_write"] / million
    )


class LLMClient:
    """Async KIMI/Moonshot client (OpenAI-compatible) with retry + cost tracking.

    Construct once per process and inject; do not new up per call.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        cost_log: CostLog | None = None,
        daily_budget_usd: Decimal | None = None,
    ) -> None:
        settings = get_settings()
        resolved_key = (
            api_key
            or os.environ.get("KIMI_API_KEY")
            or settings.llm.kimi_api_key.get_secret_value()
        )
        if not resolved_key:
            raise LLMError("KIMI_API_KEY not configured")

        resolved_url = base_url or os.environ.get("KIMI_URL") or settings.llm.kimi_url

        self._client = AsyncOpenAI(api_key=resolved_key, base_url=resolved_url)
        self.timeout_seconds = timeout_seconds or settings.llm.timeout_seconds
        self.max_retries = max_retries or settings.llm.max_retries
        self.cost_log = cost_log or CostLog()
        self.daily_budget_usd = daily_budget_usd or Decimal(str(settings.llm.cost_daily_budget_usd))

    async def acomplete(
        self,
        *,
        model: str,
        messages: Iterable[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_role: str | None = None,
        ipo_id: str | None = None,
        cache_system_prompt: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """One Chat Completions call with retry + cost tracking.

        Args:
            model:               e.g. ``moonshot-v1-128k``.
            messages:            user / assistant turns.
            system:              system prompt (str or block list).
            max_tokens:          model output cap.
            temperature:         sampling temperature.
            agent_role:          tag for cost attribution.
            ipo_id:              tag for cost attribution.
            cache_system_prompt: (ignored for KIMI, kept for API compatibility)
            extra:               extra API kwargs (e.g. tools).
        """
        self._enforce_daily_budget()

        # Build messages list: prepend system message if provided
        msg_list = self._build_messages(system, messages)
        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msg_list,
        }
        if extra:
            api_kwargs.update(extra)

        log = _log.bind(
            model=model,
            agent_role=agent_role,
            ipo_id=ipo_id,
            max_tokens=max_tokens,
        )

        started = time.monotonic()
        try:
            raw = await self._call_with_retry(api_kwargs, log)
        except RetryError as exc:
            inner = exc.last_attempt.exception() if exc.last_attempt else exc
            if isinstance(inner, RateLimitError):
                raise LLMRateLimitError("LLM rate limit retries exhausted") from inner
            if isinstance(inner, APITimeoutError):
                raise LLMTimeoutError("LLM timeout retries exhausted") from inner
            raise LLMError("LLM call failed after retries", cause=str(inner)) from inner

        elapsed = time.monotonic() - started

        usage = getattr(raw, "usage", None)
        tokens_input = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_output = int(getattr(usage, "completion_tokens", 0) or 0)
        tokens_cache_read = 0
        tokens_cache_write = 0
        cost = _compute_cost(
            model, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write
        )

        record = CostRecord(
            model=model,
            agent_role=agent_role,
            ipo_id=ipo_id,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_cache_read=tokens_cache_read,
            tokens_cache_write=tokens_cache_write,
            cost_usd=cost,
            runtime_seconds=elapsed,
            request_id=getattr(raw, "id", None),
            occurred_at_unix=time.time(),
        )
        self.cost_log.append(record)

        log.info(
            "llm_call_complete",
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=str(cost),
            runtime_seconds=round(elapsed, 3),
            request_id=record.request_id,
        )

        return LLMResponse(
            text=self._extract_text(raw),
            model=model,
            stop_reason=self._extract_finish_reason(raw),
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_cache_read=tokens_cache_read,
            tokens_cache_write=tokens_cache_write,
            cost_usd=cost,
            runtime_seconds=elapsed,
            request_id=record.request_id,
            raw=raw,
        )

    # ------------------------------------------------------------------ internals

    async def _call_with_retry(self, api_kwargs: dict[str, Any], log: Any) -> Any:
        """Call the OpenAI-compatible Chat Completions API with retry + per-call timeout.

        Retry policy is configured to raise RetryError (with last_attempt set)
        when attempts exhaust, so the caller can unwrap and translate to a
        typed LLMRateLimitError / LLMTimeoutError.
        """
        retry = AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError)),
            reraise=False,  # raise RetryError so caller can translate
        )

        async for attempt in retry:
            with attempt:
                attempt_no = attempt.retry_state.attempt_number
                log.debug("llm_call_attempt", attempt=attempt_no)
                try:
                    return await asyncio.wait_for(
                        self._client.chat.completions.create(**api_kwargs),
                        timeout=self.timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise APITimeoutError(request=None) from exc  # type: ignore[arg-type]
                except RateLimitError:
                    raise  # 429 — handled by retry policy
                except APIStatusError as exc:
                    # Other 4xx / 5xx — do not retry; raise as plain LLMError
                    raise LLMError(f"KIMI API status {exc.status_code}: {exc.message}") from exc
        raise LLMError("Retry loop exited without result")  # pragma: no cover

    def _enforce_daily_budget(self) -> None:
        if self.cost_log.total_usd() >= self.daily_budget_usd:
            raise LLMCostExceededError(
                f"Daily LLM budget {self.daily_budget_usd} USD exceeded",
                current=str(self.cost_log.total_usd()),
                budget=str(self.daily_budget_usd),
            )

    @staticmethod
    def _build_messages(
        system: str | list[dict[str, Any]] | None,
        messages: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build OpenAI-compatible message list with system message prepended."""
        msg_list: list[dict[str, Any]] = []
        if system is not None:
            if isinstance(system, str):
                msg_list.append({"role": "system", "content": system})
            elif isinstance(system, list):
                # Convert Anthropic-style block list to plain text
                text_parts = []
                for block in system:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    msg_list.append({"role": "system", "content": "\n".join(text_parts)})
            else:
                msg_list.append({"role": "system", "content": str(system)})
        msg_list.extend(list(messages))
        return msg_list

    @staticmethod
    def _extract_text(raw: Any) -> str:
        """Extract text from an OpenAI ChatCompletion response."""
        choices = getattr(raw, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        return getattr(message, "content", "") or ""

    @staticmethod
    def _extract_finish_reason(raw: Any) -> str | None:
        """Extract finish_reason from an OpenAI ChatCompletion response."""
        choices = getattr(raw, "choices", None) or []
        if not choices:
            return None
        return getattr(choices[0], "finish_reason", None)

    # -------------------------------------------------------- structured output

    async def acomplete_json(
        self,
        *,
        model: str,
        messages: Iterable[dict[str, Any]],
        response_model: type[T],
        system: str | list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_role: str | None = None,
        ipo_id: str | None = None,
        cache_system_prompt: bool = True,
        max_retries: int = 2,
        extra: dict[str, Any] | None = None,
    ) -> T:
        """Call the model and parse the response into ``response_model``.

        Re-prompts up to ``max_retries`` times on JSON / Pydantic validation
        failure (the model gets its own malformed output back with the
        ValidationError, which tends to be very effective at fixing the next try).

        Raises:
            LLMOutputValidationError: if all retries fail to produce a parseable model.
        """
        msg_list: list[dict[str, Any]] = list(messages)
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            response = await self.acomplete(
                model=model,
                messages=msg_list,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                agent_role=agent_role,
                ipo_id=ipo_id,
                cache_system_prompt=cache_system_prompt,
                extra=extra,
            )
            try:
                payload = _coerce_json(response.text)
                return response_model.model_validate(payload)
            except (ValueError, ValidationError) as exc:
                last_error = exc
                if attempt == max_retries:
                    break
                # Feed the failure back to the model and ask it to repair.
                msg_list = [
                    *msg_list,
                    {"role": "assistant", "content": response.text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response failed JSON / schema validation:\n"
                            f"{exc}\n\n"
                            "Re-emit ONLY the JSON document with the exact required fields "
                            "and types. No prose, no code fences."
                        ),
                    },
                ]
        raise LLMOutputValidationError(
            f"LLM output failed schema validation after {max_retries + 1} attempts",
            cause=str(last_error) if last_error else None,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _coerce_json(text: str) -> Any:
    """Extract a JSON object from the model output (handles ```json fences)."""
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    match = _JSON_FENCE_RE.search(text)
    if match:
        candidates.insert(0, match.group(1).strip())
    last_err: Exception | None = None
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError as exc:
            last_err = exc
    raise ValueError(f"Could not parse JSON from LLM output: {last_err}")


__all__ = (
    "DEFAULT_PRICES_USD_PER_MTOKENS",
    "CostLog",
    "CostRecord",
    "LLMClient",
    "LLMResponse",
)
