# ADR 0002: Use Claude (Sonnet 4 default, Opus 4.7 for Synthesizer) as primary LLM

- **Status**: **Superseded by [ADR 0017](0017-llm-provider-kimi-moonshot.md)**
- **Date**: 2026-05-16
- **Superseded**: 2026-05-18 (R4-6, post-v1.0 hardening)
- **Deciders**: project lead

> ⚠️ This ADR is **historical**. The runtime LLM provider was switched
> from Anthropic Claude to KIMI/Moonshot in commit `2582dab` for cost
> and latency reasons. [ADR 0017](0017-llm-provider-kimi-moonshot.md)
> documents the replacement decision; this ADR is retained for audit
> trail of the original choice.

## Context

Required by PROJECT_SPEC.md §1 technology stack.

## Decision

Use Claude (Sonnet 4 default, Opus 4.7 for Synthesizer) as primary LLM.

## Consequences

TODO: enumerate consequences in Phase 1.
