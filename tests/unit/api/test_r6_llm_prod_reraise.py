"""R6-6 — LLMClient init failure: warn-and-continue in dev, re-raise in prod.

Pre-R6-6 ``api/main.lifespan`` swallowed every LLMClient construction
error and set ``app.state.llm_client = None``. That's the right behaviour
in dev/test (no API key on CI) but in production it silently degrades to
"every LLM-backed endpoint 500s mysteriously" — much worse than crashing
at startup where the operator can fix the missing config.

Post-R6-6:
  * dev / test environments: same as before — warn (no logger import in
    main.py; we accept silent fallback) and proceed with llm_client=None.
  * production environment (Settings.environment in {"prod", "production"}):
    re-raise the original exception so the process never gets to ``yield``
    and uvicorn fails fast.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hk_ipo_agent.api import main as main_mod


class _Boom(RuntimeError):
    """Distinct error so tests can spot it explicitly."""


def _patch_llm_to_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make LLMClient(...) raise on instantiation."""

    def _ctor(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _Boom("KIMI_API_KEY missing")

    monkeypatch.setattr(main_mod, "LLMClient", _ctor)


def _patch_environment(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """Force ``main_mod.get_settings()`` to return Settings(environment=env).

    Direct env-var override doesn't work here because Settings.from_yaml_and_env
    layers ``config/settings.yaml`` ON TOP of env vars (pydantic-settings
    init-kwargs > env-vars). Monkeypatching the resolver inside main_mod is
    the cleanest way to bypass that for the test.
    """

    def _fake_get_settings():  # type: ignore[no-untyped-def]
        return SimpleNamespace(environment=env)

    monkeypatch.setattr(main_mod, "get_settings", _fake_get_settings)


@pytest.mark.asyncio
async def test_lifespan_dev_swallows_llm_init_error_and_sets_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-6 — dev: app.state.llm_client is None; lifespan does NOT re-raise."""
    _patch_environment(monkeypatch, "dev")
    _patch_llm_to_fail(monkeypatch)

    app = main_mod.create_app()
    async with main_mod.lifespan(app):
        assert app.state.llm_client is None


@pytest.mark.asyncio
async def test_lifespan_prod_reraises_llm_init_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-6 — prod: lifespan re-raises the original exception.

    The "fail fast" production contract: a missing API key in production
    must surface as a startup crash, not as silently broken LLM-backed
    endpoints discovered hours later.
    """
    _patch_environment(monkeypatch, "prod")
    _patch_llm_to_fail(monkeypatch)

    app = main_mod.create_app()
    with pytest.raises(_Boom):
        async with main_mod.lifespan(app):
            pass  # pragma: no cover — lifespan must raise before yield


@pytest.mark.asyncio
async def test_lifespan_production_alias_also_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-6 — ``environment="production"`` (long spelling) is treated the same as ``"prod"``."""
    _patch_environment(monkeypatch, "production")
    _patch_llm_to_fail(monkeypatch)

    app = main_mod.create_app()
    with pytest.raises(_Boom):
        async with main_mod.lifespan(app):
            pass  # pragma: no cover


@pytest.mark.asyncio
async def test_lifespan_prod_succeeds_when_llm_client_constructs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-6 — happy prod path: LLMClient builds fine → lifespan completes normally."""
    _patch_environment(monkeypatch, "prod")

    class _Stub:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.daily_budget_usd = kwargs.get("daily_budget_usd")

    monkeypatch.setattr(main_mod, "LLMClient", _Stub)

    app = main_mod.create_app()
    async with main_mod.lifespan(app):
        assert app.state.llm_client is not None
