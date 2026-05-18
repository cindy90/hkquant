"""BaseAgent abstract class per PROJECT_SPEC.md §7.

Every concrete agent inherits ``BaseAgent`` and implements
``async def run(ctx: AgentContext) -> AgentOutput``. The base class
provides:

- Frontmatter-aware prompt loader (``_load_prompt``)
- ScoreCard parsing + report stripping helpers
- LLM call wrapper that enforces JSON output + retries via
  ``LLMClient.acomplete_json``
- Cost / runtime accounting → folded into the returned ``AgentOutput``

Pattern borrowed from
``D:/自定义工具/港股数据分析/港股基石建模/港股研究agent/src/agents/base.py`` +
``_template.py`` (see ADR 0009), but rewritten for:
- async IO
- mandatory citations
- strict Pydantic ``AgentOutput`` output
- single-provider KIMI/Moonshot LLM (OpenAI-compatible)
- LangGraph-compatible state mutation via ``WorkflowExtras``
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, cast

from pydantic import BaseModel

from ..common.enums import AgentRole, Confidence
from ..common.exceptions import CitationRequiredError
from ..common.llm_client import LLMClient, LLMResponse
from ..common.schemas import (
    AgentOutput,
    Citation,
    DataSource,
    Finding,
    ProspectusExtraction,
)
from ..valuation.base import MarketData
from .scoring import BaseScoreCard, extract_json_block, strip_json_blocks
from .workflow_extras import WorkflowExtras

_ = MarketData  # type re-export marker

# ---------------------------------------------------------------------------
# Prompt loader (frontmatter-aware)
# ---------------------------------------------------------------------------


# Repo root: src/hk_ipo_agent/agents/base.py -> ../../../prompts/
_PROMPTS_ROOT: Path = Path(__file__).resolve().parents[3] / "prompts"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def load_prompt(prompt_path: str) -> tuple[str, dict[str, Any]]:
    """Load a prompt file. Returns ``(body_without_frontmatter, frontmatter_dict)``.

    ``prompt_path`` is relative to ``prompts/`` (e.g. ``"agents/policy.md"``).
    Missing frontmatter is OK — returns empty dict.
    """
    full = (_PROMPTS_ROOT / prompt_path).read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(full)
    if not m:
        return full, {}

    # Minimal YAML-ish parser — handles key:value and key:list-of-strings
    # one-per-line ("- item"). Strips inline ``# comment`` annotations.
    frontmatter: dict[str, Any] = {}
    cur_key: str | None = None
    for raw_line in m.group(1).splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") or line.startswith("\t- "):
            if cur_key and isinstance(frontmatter.get(cur_key), list):
                item = line.strip()[2:].strip()
                # Strip inline comment.
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                frontmatter[cur_key].append(item)
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            # Strip inline comment from value.
            if "#" in val:
                val = val.split("#", 1)[0].strip()
            if not val:
                frontmatter[key] = []
                cur_key = key
            else:
                frontmatter[key] = val
                cur_key = key
    body = full[m.end() :]
    return body, frontmatter


# ---------------------------------------------------------------------------
# AgentContext — runtime state passed to every agent
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Cross-agent runtime context.

    Required: ``ipo_id``, ``extraction``, ``market_data``, ``llm_client``.
    Optional: tools (``prospectus_tool`` / ``ifind_tool`` / ``kb_tool``) +
    ``extras`` (shared NACS signals).
    """

    ipo_id: str
    extraction: ProspectusExtraction
    market_data: MarketData
    llm_client: LLMClient

    # Shared cross-agent state
    extras: WorkflowExtras = field(default_factory=WorkflowExtras)

    # Tool injection — concrete tools defined in ``agents/tools/``.
    # Type stays loose (Any) to avoid heavy circular imports; concrete
    # ``isinstance`` checks happen in each agent if needed.
    prospectus_tool: Any = None
    ifind_tool: Any = None
    kb_tool: Any = None


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------


class BaseAgent(ABC):
    """Abstract base for every expert agent.

    Subclasses set:
    - ``role``: ``AgentRole`` enum (used as YAML lookup key)
    - ``prompt_path``: relative path under ``prompts/`` (e.g. ``"agents/policy.md"``)
    - ``score_card_class``: optional ``BaseScoreCard`` subclass for typed scores

    R4-1 / R4-3: the model + max_tokens + temperature are now resolved
    at call time from ``config/llm_models.yaml`` via
    :func:`hk_ipo_agent.common.settings.resolve_agent_model_config`, keyed
    on ``agents.<role.value>``. The legacy ``model`` ClassVar is kept as
    a fallback for tests that subclass without a corresponding YAML row.
    """

    role: ClassVar[AgentRole]
    prompt_path: ClassVar[str]
    model: ClassVar[str] = "moonshot-v1-128k"  # fallback only; YAML wins
    score_card_class: ClassVar[type[BaseScoreCard] | None] = None

    def _resolved_model_config(self) -> dict[str, Any]:
        """R4-1 / R4-3 — resolve this agent's runtime model config."""
        from ..common.settings import resolve_agent_model_config

        return resolve_agent_model_config(
            f"agents.{self.role.value}",
            default_model=self.model,
        )

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentOutput:
        """Produce an ``AgentOutput`` for the IPO under analysis.

        Implementations should:
        1. Optionally populate ``ctx.extras`` with NACS-style signals
           (e.g. policy_agent → ``extras.regime_score``).
        2. Render the prompt (load_prompt + augmented with schema_instruction).
        3. Call ``self._call_llm(...)`` and parse the ScoreCard.
        4. Build ``Finding`` objects with mandatory citations and return.
        """

    # ----------------------------------------------------------------- helpers

    def _load_prompt_body(self) -> tuple[str, dict[str, Any]]:
        """Load this agent's prompt body + frontmatter."""
        return load_prompt(self.prompt_path)

    # R4-7 — inherited_inputs aliasing for fields whose declared name differs
    # from the WorkflowExtras attribute. Without this we'd false-fail on
    # "sponsor_track_record" (extras has plural ".sponsor_track_records")
    # or "ai_gilding_signal" (extras has ".ai_gilding_flag").
    _INHERITED_INPUT_ALIASES: ClassVar[dict[str, str]] = {
        "sponsor_track_record": "sponsor_track_records",
        "ai_gilding_signal": "ai_gilding_flag",
    }

    @classmethod
    def _verify_inherited_inputs(
        cls,
        frontmatter: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """R4-7 — fail-loud if any declared ``inherited_inputs`` is missing.

        Pre-R4-7 the frontmatter's ``inherited_inputs`` list was parsed
        but never validated — an agent could declare it depends on
        ``regime_score`` while the upstream tool never populated it, and
        the LLM would silently receive a sentinel placeholder. R4-7 turns
        this into a startup contract failure.

        Resolution order for each declared key:
        1. ``ctx.extras.get(<key>)`` (typed field on WorkflowExtras)
        2. ``ctx.extras.misc[<key>]`` (untyped fallback)
        3. ``ctx.extras.get(<aliased_key>)`` (per _INHERITED_INPUT_ALIASES)
        4. Attribute named ``<key>`` on ``ctx.kb_tool`` (if any)

        Empty lists / empty dicts / ``None`` all count as "missing".

        Raises:
            MissingInheritedInputError: if any declared key resolves to
                None / empty. Lists every offending key in the message.
        """
        from ..common.exceptions import MissingInheritedInputError

        declared = frontmatter.get("inherited_inputs") or []
        if not declared:
            return  # no contract → no-op

        def _is_missing(v: Any) -> bool:
            """Treat None / empty list / empty dict as 'not populated'."""
            return v is None or v in ([], {})

        missing: list[str] = []
        for key_raw in declared:
            # Frontmatter list items may carry trailing comments stripped
            # already by load_prompt, but trim whitespace defensively.
            key = str(key_raw).strip()
            if not key:
                continue
            aliased = cls._INHERITED_INPUT_ALIASES.get(key, key)
            # 1 + 2 + 3: try extras (typed + misc + alias)
            val = ctx.extras.get(key)
            if _is_missing(val):
                val = ctx.extras.get(aliased)
            # 4: try kb_tool attribute
            if _is_missing(val) and ctx.kb_tool is not None:
                val = getattr(ctx.kb_tool, key, None) or getattr(ctx.kb_tool, aliased, None)
            if _is_missing(val):
                missing.append(key)

        if missing:
            raise MissingInheritedInputError(
                f"agent {cls.__name__} declares inherited_inputs but the "
                f"following are not populated in ctx.extras / ctx.kb_tool: "
                f"{missing}. Wire the upstream tool dispatch before "
                f"BaseAgent.run() — see PLAN R4-7."
            )

    async def _call_llm(
        self,
        ctx: AgentContext,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Plain text LLM call with cost attribution to this agent.

        R4-1 / R4-3: model + max_tokens + temperature are resolved from
        config/llm_models.yaml when not explicitly overridden by caller.
        """
        cfg = self._resolved_model_config()
        return await ctx.llm_client.acomplete(
            model=cfg["model"],
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens if max_tokens is not None else cfg["max_tokens"],
            temperature=temperature if temperature is not None else cfg["temperature"],
            agent_role=self.role.value,
            ipo_id=ctx.ipo_id,
        )

    async def _call_llm_typed(
        self,
        ctx: AgentContext,
        *,
        system: str,
        user: str,
        response_model: type[BaseModel],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> tuple[BaseModel, LLMResponse]:
        """Call the LLM and parse into ``response_model``.

        Uses ``acomplete_json`` which retries on validation failure.
        Note: the wrapped client doesn't return the raw ``LLMResponse``
        when going through ``acomplete_json`` — callers that need cost
        accounting should call ``_call_llm`` + parse manually.

        For Phase 5 we accept this trade-off and reconstruct partial cost
        info from the cost log.

        R4-1 / R4-3: model + max_tokens + temperature default to config/llm_models.yaml.
        """
        cfg = self._resolved_model_config()
        before = ctx.llm_client.cost_log.total_usd()
        start = time.monotonic()
        model = await ctx.llm_client.acomplete_json(
            model=cfg["model"],
            messages=[{"role": "user", "content": user}],
            system=system,
            response_model=response_model,
            max_tokens=max_tokens if max_tokens is not None else cfg["max_tokens"],
            temperature=temperature if temperature is not None else cfg["temperature"],
            agent_role=self.role.value,
            ipo_id=ctx.ipo_id,
        )
        after = ctx.llm_client.cost_log.total_usd()
        # Build a partial LLMResponse for ergonomic reuse.
        pseudo = LLMResponse(
            text="",
            model=cfg["model"],
            stop_reason="end_turn",
            tokens_input=0,
            tokens_output=0,
            tokens_cache_read=0,
            tokens_cache_write=0,
            cost_usd=Decimal(str(after - before)),
            runtime_seconds=time.monotonic() - start,
            request_id=None,
            raw=None,
        )
        return model, pseudo

    def _parse_score_card(
        self,
        text: str,
    ) -> BaseScoreCard | None:
        """Best-effort parse of a fenced ``json`` block into the agent's ScoreCard.

        Returns ``None`` if no fence / parse fails / no ScoreCard class set.
        """
        if self.score_card_class is None:
            return None
        payload = extract_json_block(text)
        if payload is None:
            return None
        try:
            return self.score_card_class.model_validate(payload)
        except Exception:
            return None

    def _strip_score_card_block(self, text: str) -> str:
        """Remove ```json``` fences from the LLM output (for human-readable report)."""
        return strip_json_blocks(text)

    @staticmethod
    def _pick_extraction_citations(
        extraction: ProspectusExtraction,
        evidence_pages: list[int] | None = None,
    ) -> list[Citation]:
        """Build Citation list, optionally pinned to evidence pages.

        Falls back to the first available citation in the extraction
        (financials → risks). If no evidence is available anywhere,
        raises :class:`CitationRequiredError` rather than fabricating a
        page-1 citation.

        R1-3: pre-fix returned ``[Citation(page=1)]`` as a silent fallback,
        which violated CLAUDE.md strict constraint "every Finding must
        trace back to a prospectus page". Callers are now expected to
        catch the exception and emit an uncertainty_flag-only finding
        instead of a sham one.
        """
        if evidence_pages:
            return [Citation(page=p) for p in evidence_pages]
        if extraction.financials:
            return [extraction.financials[0].citation]
        if extraction.risk_factors:
            return [extraction.risk_factors[0].citation]
        raise CitationRequiredError(
            "no citation available in extraction: financials, risk_factors, "
            "and evidence_pages are all empty. Caller must handle this case "
            "explicitly (emit uncertainty_flag instead of forging a citation)."
        )

    @staticmethod
    def _make_finding(
        statement: str,
        evidence: str,
        citations: list[Citation],
        *,
        confidence: str = "medium",
    ) -> Finding:
        """Constructor with import-side default values."""
        return Finding(
            statement=statement,
            evidence=evidence,
            citations=citations,
            confidence=Confidence(confidence),
        )

    @staticmethod
    def _make_data_source(source: str, detail: str) -> DataSource:
        return DataSource(source=cast(Any, source), detail=detail)


__all__ = (
    "AgentContext",
    "BaseAgent",
    "load_prompt",
)
