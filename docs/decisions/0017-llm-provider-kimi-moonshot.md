# ADR 0017 — LLM Provider: Anthropic Claude → KIMI/Moonshot

- **Status**: Accepted
- **Date**: 2026-05-18
- **Deciders**: Project lead
- **Supersedes**: [ADR 0002](0002-claude-as-primary-llm.md)
- **Phase**: R4 (post-v1.0 hardening) — although the switch itself happened
  in commit `2582dab` during v1.0; this ADR documents it retroactively.

## Context

ADR 0002 (2026-05-16) selected Anthropic Claude (Sonnet 4 for worker
agents + Opus 4.7 for the Synthesizer) as the primary LLM. PROJECT_SPEC.md
§1 captured the same decision.

Between v1.0 tag (Phase 10 release) and the post-v1.0 review, the
runtime provider was switched to KIMI/Moonshot in commit `2582dab`
("chore(llm): switch LLM provider from Anthropic Claude to KIMI/Moonshot").
The switch happened without a corresponding ADR update — the
2026-05-17 full-codebase review flagged this as a top-priority
governance gap (8 review agent reports converged on the same finding).

The actual driving reasons for the switch:

1. **Cost**: KIMI moonshot-v1-128k is roughly 5-10x cheaper than
   Claude Sonnet 4 for the project's typical 4k-input / 1.5k-output
   per-agent call profile.
2. **Latency / regional availability**: HK-based deployment latency
   to KIMI's Beijing endpoint is consistently sub-300ms; Anthropic's
   us-east-1 endpoint adds ~250ms baseline.
3. **OpenAI SDK compatibility**: KIMI exposes an OpenAI-compatible API
   at `https://api.moonshot.cn/v1` so the existing `openai>=1.0` SDK in
   `pyproject.toml:15` works unchanged. No new dependency.
4. **No measured quality regression** on the v1.0 374-sample backtest
   (Phase 8 calibration archive shows IC parity with prior Claude
   baselines — see backtest/metrics.py + nacs_v8_baselines.json).

## Decision

**Use KIMI moonshot-v1-128k as the runtime LLM for all agents,
the Synthesizer, and extraction tasks.** Configuration lives in
`config/llm_models.yaml`. Per-role model + max_tokens + temperature
are explicit and overridable.

The Synthesizer **retains a distinct config entry** (`agents.synthesizer.*`
in `llm_models.yaml`) even though the model name is currently identical
to worker agents. This preserves the ability to route the Synthesizer
to a stronger model (or back to Claude Opus 4.7) by a single YAML change,
without re-touching code.

## Consequences

### Positive

- Single config-driven switching point. Future provider migrations
  change one YAML file, not every agent call site.
- Per-role observable cost / quality — `llm_models.yaml` is the audit
  surface for who calls what.
- ADR 0002 is now formally Superseded; the governance trail is honest
  about what's running in production.

### Negative

- Loss of model-tier differentiation: pre-switch, Synthesizer used
  Opus 4.7 (stronger reasoning) while workers used Sonnet 4. Under
  KIMI, all roles share moonshot-v1-128k. The Phase 8 calibration
  archive shows no quality regression, but high-complexity edge cases
  (complex multi-signal Synthesizer outputs) have not been re-tested
  at scale post-switch.
- Vendor lock-in risk migrates from Anthropic to Moonshot AI. Mitigated
  by OpenAI-SDK compatibility — any OpenAI-compatible endpoint can be
  dropped in by changing `HK_IPO__LLM__KIMI_URL`.

### Neutral

- `pyproject.toml` keeps `openai>=1.0`; `anthropic` SDK is no longer
  imported (already absent from the dependency list).
- Existing Anthropic API key env var slot (`HK_IPO__LLM__ANTHROPIC_API_KEY`)
  is retained in `Settings.LLMSettings` for any future opt-in fallback
  experiments, but is **not used at runtime**.

## Verification

- `config/llm_models.yaml` has per-role entries (8 worker agents +
  synthesizer + 2 extraction tasks).
- `R4-1` introduces `common/settings.resolve_agent_model(role)` so
  every call site reads from YAML instead of hardcoding model strings.
- Pre-switch hardcoded `"moonshot-v1-128k"` literals in 6 files
  (`agents/base.py:151`, `critic/bull.py:46`, `critic/bear.py:25`,
  `critic/devils_advocate.py:29`, `synthesizer/synthesizer.py:86`,
  `prediction_registry/snapshot.py:118`) are removed by R4-1.

## Future migration triggers

This ADR will be revised (or a new ADR will supersede it) if any of:

1. KIMI/Moonshot pricing changes by > 30% or rate limits become a
   throughput bottleneck.
2. The Phase 8 IC parity gap vs. Claude widens beyond `DEFAULT_IC_TOLERANCE`
   on the 374-sample baseline.
3. A vendor incident degrades availability below the project's SLO
   target.

In any of these cases, the YAML-only switching surface (R4-1) means
the migration is a one-line config change + a re-run of Phase 8
calibration to confirm parity.

## Links

- Switching commit: `2582dab` ("chore(llm): switch LLM provider from
  Anthropic Claude to KIMI/Moonshot")
- Supersedes: [ADR 0002](0002-claude-as-primary-llm.md)
- Plan: [docs/PLAN_post_v1.0.md](../PLAN_post_v1.0.md) §6 R4-6
- Implementation: `config/llm_models.yaml` + `common/settings.resolve_agent_model`
- Pricing reference: <https://platform.moonshot.cn/docs/pricing> (vendor)
