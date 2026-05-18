"""Tests for `hk_ipo_agent.common.settings`."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from hk_ipo_agent.common.exceptions import ConfigurationError
from hk_ipo_agent.common.settings import DatabaseSettings, Settings, _load_yaml


def test_database_url_async_psycopg() -> None:
    # Test fixture credentials — not real secrets.
    _PW = "s3cr3t"  # pragma: allowlist secret
    db = DatabaseSettings(
        host="db.example.com",
        port=5433,
        name="hki",
        user="alice",
        password=SecretStr(_PW),
    )
    assert db.url == f"postgresql+asyncpg://alice:{_PW}@db.example.com:5433/hki"
    assert db.sync_url == f"postgresql+psycopg://alice:{_PW}@db.example.com:5433/hki"


def test_settings_default_values() -> None:
    s = Settings()
    assert s.environment in {"dev", "staging", "prod"}
    assert s.database.port == 5432
    assert s.api.port == 8000
    assert s.log_level == "INFO"


def test_load_yaml_missing_returns_empty(tmp_path: pytest.TempPathFactory) -> None:
    missing = tmp_path / "nope.yaml"  # type: ignore[operator]
    assert _load_yaml(missing) == {}


def test_load_yaml_invalid_top_level(tmp_path: pytest.TempPathFactory) -> None:
    bad = tmp_path / "bad.yaml"  # type: ignore[operator]
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        _load_yaml(bad)


def test_password_secret_redacted_in_repr() -> None:
    db = DatabaseSettings(password=SecretStr("topsecret"))
    text = repr(db)
    assert "topsecret" not in text


def test_env_override_via_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HK_IPO__DATABASE__HOST", "override.example.com")
    monkeypatch.setenv("HK_IPO__DATABASE__PORT", "9999")
    s = Settings()
    assert s.database.host == "override.example.com"
    assert s.database.port == 9999


# ---------------------------------------------------------------------------
# R2-1 + R2-7 — production environment hard guards
# ---------------------------------------------------------------------------


def test_settings_prod_requires_hitl_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2-1 — production env must fail-loud if HITL is not enabled.

    CLAUDE.md «HITL 默认 bypass，生产 env 强制开» was previously documented
    but not enforced in code. With this guard, an operator that forgot to
    set ``HK_IPO__ORCHESTRATOR__ENABLE_HITL=true`` in prod gets a startup
    error rather than a silently bypassed human-in-the-loop checkpoint.
    """
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "prod")
    # Default enable_hitl is False; do not override it.
    monkeypatch.setenv(
        "HK_IPO__AUTH__JWT_SECRET", "prod-secret-min-32-chars-long-enough-1"
    )  # avoid R2-7
    with pytest.raises(ConfigurationError, match="HITL must be enabled in production"):
        Settings()


def test_settings_prod_with_hitl_enabled_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production env + HITL on + non-default JWT secret → no raise."""
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "prod")
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")
    monkeypatch.setenv("HK_IPO__AUTH__JWT_SECRET", "prod-secret-min-32-chars-long-enough-1")
    s = Settings()
    assert s.environment.lower() in {"prod", "production"}
    assert s.orchestrator.enable_hitl is True


def test_settings_dev_env_does_not_require_hitl() -> None:
    """Dev env tolerates default enable_hitl=False (CLAUDE.md baseline)."""
    s = Settings()
    assert s.environment == "dev"
    # Should not raise even though enable_hitl defaults to False.


def test_settings_prod_requires_jwt_secret_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2-7 — production env must reject the default JWT secret literal.

    The default ``"change-me-min-32-chars-long-secret-here"`` in code is a
    placeholder; allowing it in prod would let an attacker forge tokens
    against any deployment whose operator forgot to override.
    """
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "prod")
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")  # bypass R2-1
    # Explicitly set jwt_secret to the placeholder value. CI sets it to a
    # non-default fixture via job-level env, which would mask the guard;
    # this test must pin the exact placeholder it's checking for.
    monkeypatch.setenv(
        "HK_IPO__AUTH__JWT_SECRET",
        "change-me-min-32-chars-long-secret-here",
    )
    with pytest.raises(ConfigurationError, match="JWT secret"):
        Settings()


def test_settings_prod_accepts_non_default_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production + custom JWT secret + HITL on → no raise."""
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "prod")
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")
    monkeypatch.setenv("HK_IPO__AUTH__JWT_SECRET", "a-real-prod-secret-not-the-default-one-12345")
    s = Settings()
    assert s.auth.jwt_secret.get_secret_value() != "change-me-min-32-chars-long-secret-here"


def test_settings_production_alias_also_triggers_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards must trigger on environment="production" alias too, not just "prod"."""
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "production")
    # default enable_hitl = False → expect HITL guard to fire first
    with pytest.raises(ConfigurationError, match="HITL must be enabled"):
        Settings()


# ---------------------------------------------------------------------------
# R4-1 + R4-3 — single entry point for "what model + temp does this role use?"
# ---------------------------------------------------------------------------


def test_resolve_agent_model_reads_yaml_for_known_role() -> None:
    """R4-1 — known role in llm_models.yaml returns the configured model."""
    from hk_ipo_agent.common.settings import resolve_agent_model

    assert resolve_agent_model("agents.fundamental") == "moonshot-v1-128k"
    assert resolve_agent_model("agents.synthesizer") == "moonshot-v1-128k"
    assert resolve_agent_model("extraction.prospectus") == "moonshot-v1-128k"


def test_resolve_agent_model_default_for_unknown_role() -> None:
    """R4-1 — unknown roles return the caller-supplied default."""
    from hk_ipo_agent.common.settings import resolve_agent_model

    assert resolve_agent_model("agents.does_not_exist", default="fallback") == "fallback"
    assert resolve_agent_model("totally.bogus.path", default="x") == "x"


def test_resolve_agent_model_handles_malformed_yaml_path() -> None:
    """R4-1 — non-dict cursor along the path returns default cleanly."""
    from hk_ipo_agent.common.settings import resolve_agent_model

    # agents.fundamental is a dict; .nonsense is not a key inside it.
    assert resolve_agent_model("agents.fundamental.nonsense", default="d") == "d"


def test_resolve_agent_model_config_returns_full_triple() -> None:
    """R4-3 — full config triple (model / max_tokens / temperature)."""
    from hk_ipo_agent.common.settings import resolve_agent_model_config

    cfg = resolve_agent_model_config("agents.sentiment")
    assert cfg["model"] == "moonshot-v1-128k"
    assert cfg["max_tokens"] == 4096
    assert cfg["temperature"] == pytest.approx(0.4)  # per llm_models.yaml


def test_resolve_agent_model_config_falls_back_on_unknown() -> None:
    """R4-3 — unknown role returns supplied defaults."""
    from hk_ipo_agent.common.settings import resolve_agent_model_config

    cfg = resolve_agent_model_config(
        "agents.bogus",
        default_model="m",
        default_max_tokens=128,
        default_temperature=0.5,
    )
    assert cfg == {"model": "m", "max_tokens": 128, "temperature": 0.5}
