"""R4-4 — Jinja2-aware prompt renderer.

CLAUDE.md §提示词约束 §4: "所有提示词在 LLM 调用前都必须经过 Jinja2 渲染
（注入 schema、上下文）". Pre-R4-4 the project had ``load_prompt`` which
parsed frontmatter but never ran Jinja2 — string interpolation was done
ad-hoc in each agent's ``_call_llm`` call site via f-string concat. R4-4
introduces a single entry point ``render_prompt`` so prompt templates
can use Jinja2 ``{{ var }}`` placeholders + ``schema_instruction`` is
auto-injected when a ScoreCard class is provided.

Design choices:

1. **Backward compatible**: prompts without ``{{ }}`` placeholders are
   passed through unchanged. The existing 21 prompt files don't need
   to migrate in this commit — they get the schema_instruction
   auto-append benefit for free, and new prompts can opt into Jinja2.

2. **StrictUndefined**: any ``{{ var }}`` reference that's missing from
   the caller's kwargs raises immediately. This is the
   "missing-variable bug surfaces at first call, not silently in prod"
   contract.

3. **Schema auto-injection**: passing ``score_card_class`` appends the
   ``schema_instruction(card)`` block to the rendered body. This kills
   the dead-code finding from the 2026-05-17 review (the helper was
   defined but no caller used it).

See docs/PLAN_post_v1.0.md §6 R4-4.
"""

from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from .base import load_prompt
from .scoring import BaseScoreCard, schema_instruction

# Detect Jinja2 placeholder presence so we can short-circuit for legacy
# prompts that don't use any. ``{{`` / ``{%`` are the two relevant
# delimiters; ``{#`` is comments (irrelevant). Pre-compiled for speed.
_JINJA_DETECT_RE = re.compile(r"\{\{|\{%")


# A single Environment instance is fine — StrictUndefined makes it safe
# to reuse across agents; no state is held between renders.
_ENV = Environment(  # autoescape OFF for non-HTML prompt text
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)


class PromptRenderError(RuntimeError):
    """Raised when prompt rendering fails (missing var, syntax error, etc.)."""


def render_prompt(
    prompt_path: str,
    *,
    score_card_class: type[BaseScoreCard] | None = None,
    **vars: Any,
) -> tuple[str, dict[str, Any]]:
    """Load + render a prompt; return ``(rendered_body, frontmatter)``.

    Args:
        prompt_path: relative under ``prompts/`` (e.g. ``"agents/policy.md"``).
        score_card_class: if provided, appends the ScoreCard JSON schema
            block to the body so the LLM knows the output contract.
        **vars: Jinja2 template variables.

    Behaviour:

    - Prompts WITHOUT ``{{ }}`` / ``{% %}`` placeholders are returned
      verbatim (still get the schema_instruction append if requested).
      This is the migration ramp: existing prompts work unchanged.

    - Prompts WITH placeholders are rendered with StrictUndefined.
      Missing variables raise :class:`PromptRenderError`.

    - Syntax errors in templates also raise PromptRenderError, with
      the underlying Jinja2 exception preserved as ``__cause__``.

    Examples::

        body, fm = render_prompt("agents/policy.md")
        body, fm = render_prompt("agents/sentiment.md", score_card_class=SentimentScoreCard)
        body, fm = render_prompt("agents/new_jinja_aware.md", ipo_id="0001.HK")
    """
    body, frontmatter = load_prompt(prompt_path)

    # Jinja2 render only when actually needed; this avoids parsing every
    # existing legacy prompt that uses `{0}` placeholders or Chinese
    # punctuation Jinja could misinterpret.
    if _JINJA_DETECT_RE.search(body):
        try:
            template = _ENV.from_string(body)
            body = template.render(**vars)
        except UndefinedError as exc:
            raise PromptRenderError(
                f"Prompt {prompt_path!r} references variable not supplied to "
                f"render_prompt(...): {exc}. Pass it via **vars or remove the "
                "placeholder from the template."
            ) from exc
        except Exception as exc:
            raise PromptRenderError(f"Prompt {prompt_path!r} Jinja2 render failed: {exc}") from exc

    if score_card_class is not None:
        body = body.rstrip() + "\n\n" + schema_instruction(score_card_class).lstrip()

    return body, frontmatter


__all__ = ("PromptRenderError", "render_prompt")
