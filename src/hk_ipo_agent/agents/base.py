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

import dataclasses
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, cast

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..common.enums import AgentRole, Confidence
from ..common.exceptions import (
    CitationRequiredError,
    MissingInheritedInput,
    PromptFrontmatterError,
)
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
# Prompt loader (frontmatter-aware + Jinja2 include support per ADR 0019).
# Variable interpolation + score_card schema_instruction append live in
# ``agents/prompt_renderer.py`` (R4-4); this module's load_prompt resolves
# ``{% include %}`` so BaseAgent doesn't feed raw template strings to LLMs.
# ---------------------------------------------------------------------------


# Repo root: src/hk_ipo_agent/agents/base.py -> ../../../prompts/
_PROMPTS_ROOT: Path = Path(__file__).resolve().parents[3] / "prompts"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# Jinja2 environment for `{% include %}` resolution only. Does NOT interpolate
# user-supplied variables (that's prompt_renderer.render_prompt's job).
# autoescape OFF because prompts are not HTML.
_INCLUDE_ENV: Environment = Environment(
    loader=FileSystemLoader(str(_PROMPTS_ROOT)),
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)


# Allowed `WorkflowExtras` field names for `requires_extras:` validation
# (ADR 0019). Computed once at import time.
def _workflow_extras_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(WorkflowExtras)}


_WORKFLOW_EXTRAS_FIELDS: set[str] = _workflow_extras_field_names()


class PromptFrontmatter(BaseModel):
    """Schema for `prompts/agents/*.md` frontmatter (ADR 0019).

    Fields:
    - **Required**: `role`, `version`, `last_updated`, `input_schema`,
      `output_schema`
    - **Optional**: `score_card` (must resolve to a `BaseScoreCard` subclass),
      `requires_extras` (runtime-asserted ctx.extras keys, 1:1 to
      `WorkflowExtras` field names), `inherited_inputs` (documentation only),
      `precomputed_inputs` (documentation only), `changelog` (free text)
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    version: str
    last_updated: date
    input_schema: str
    output_schema: str

    score_card: str | None = None
    requires_extras: list[str] = Field(default_factory=list)
    inherited_inputs: list[str] = Field(default_factory=list)
    precomputed_inputs: list[str] = Field(default_factory=list)
    changelog: str | None = None

    @field_validator("version")
    @classmethod
    def _version_semver_lite(cls, v: str) -> str:
        # Accept "1.2", "1.2.3", "1.0" — at minimum one dot, all numeric parts.
        parts = v.split(".")
        if len(parts) < 2 or not all(p.isdigit() for p in parts):
            raise ValueError(f"version must be semver-lite (e.g. '1.2', '1.2.3'), got {v!r}")
        return v

    @field_validator("requires_extras")
    @classmethod
    def _requires_extras_must_match_workflow_extras(cls, v: list[str]) -> list[str]:
        unknown = [k for k in v if k not in _WORKFLOW_EXTRAS_FIELDS]
        if unknown:
            raise ValueError(
                f"requires_extras contains keys not present in WorkflowExtras: "
                f"{unknown}. Valid keys: {sorted(_WORKFLOW_EXTRAS_FIELDS)}"
            )
        return v


def _parse_frontmatter_raw(text: str) -> dict[str, Any]:
    """Minimal YAML-ish parser — handles key:value and key:list-of-strings
    one-per-line ("- item"). Strips inline ``# comment`` annotations.

    Returns empty dict if `text` is empty.
    """
    frontmatter: dict[str, Any] = {}
    cur_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") or line.startswith("\t- "):
            if cur_key and isinstance(frontmatter.get(cur_key), list):
                item = line.strip()[2:].strip()
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                frontmatter[cur_key].append(item)
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if "#" in val:
                val = val.split("#", 1)[0].strip()
            if not val:
                frontmatter[key] = []
                cur_key = key
            else:
                frontmatter[key] = val
                cur_key = key
    return frontmatter


def load_prompt(prompt_path: str, *, validate: bool = False) -> tuple[str, dict[str, Any]]:
    """Load a prompt file. Returns ``(rendered_body, frontmatter_dict)``.

    ``prompt_path`` is relative to ``prompts/`` (e.g. ``"agents/policy.md"``).
    Missing frontmatter is OK — returns empty dict.

    The body is run through Jinja2 to resolve ``{% include %}`` directives
    (ADR 0019 §4 — every agent card includes ``system/agent_common.md``).
    Variable interpolation + ``score_card`` schema_instruction append are
    handled by :func:`agents.prompt_renderer.render_prompt` (R4-4).

    If ``validate=True`` (ADR 0019), frontmatter is run through
    :class:`PromptFrontmatter` Pydantic schema; failure raises
    :class:`PromptFrontmatterError` with details.
    """
    full = (_PROMPTS_ROOT / prompt_path).read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(full)
    if not m:
        return _INCLUDE_ENV.from_string(full).render(), {}

    frontmatter = _parse_frontmatter_raw(m.group(1))
    body_raw = full[m.end() :]

    if validate:
        try:
            PromptFrontmatter.model_validate(frontmatter)
        except ValidationError as exc:
            raise PromptFrontmatterError(
                f"frontmatter validation failed for {prompt_path}: {exc}",
                prompt_path=prompt_path,
            ) from exc

    body = _INCLUDE_ENV.from_string(body_raw).render()
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

    # Class-level frontmatter cache (one parse per agent class per process,
    # ADR 0019). Validated frontmatter is reused across all LLM calls for
    # that agent.
    _cached_frontmatter: ClassVar[dict[str, Any] | None] = None

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
        """Load this agent's prompt body + frontmatter (with Jinja2 includes).

        Frontmatter is cached per class. Validation runs on first load.
        """
        body, fm = load_prompt(self.prompt_path, validate=True)
        # Cache only once per class (frontmatter doesn't change at runtime).
        if type(self)._cached_frontmatter is None:
            type(self)._cached_frontmatter = fm
        return body, fm

    def _frontmatter(self) -> dict[str, Any]:
        """Return cached frontmatter; populates cache on first call."""
        if type(self)._cached_frontmatter is None:
            self._load_prompt_body()
        return type(self)._cached_frontmatter or {}

    def _assert_required_extras(self, ctx: AgentContext) -> None:
        """ADR 0019 hard edge: raise ``MissingInheritedInput`` if any key
        listed in the prompt's ``requires_extras:`` frontmatter field is
        ``None`` on ``ctx.extras`` at LLM call time.

        Called by ``_call_llm`` / ``_call_llm_typed`` before every LLM round.
        """
        required: list[str] = self._frontmatter().get("requires_extras", []) or []
        missing = [key for key in required if getattr(ctx.extras, key, None) is None]
        if missing:
            raise MissingInheritedInput(
                f"agent {self.role.value} requires ctx.extras keys "
                f"{missing} but they are None / unset. See ADR 0019 + ADR 0005 §2.",
                agent_role=self.role.value,
                missing_keys=missing,
                prompt_path=self.prompt_path,
            )

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
        ADR 0019: ``requires_extras`` is hard-asserted before every LLM call.
        """
        self._assert_required_extras(ctx)
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
        self._assert_required_extras(ctx)
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
    "PromptFrontmatter",
    "load_prompt",
)
