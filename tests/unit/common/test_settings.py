"""Tests for `hk_ipo_agent.common.settings`."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from hk_ipo_agent.common.exceptions import ConfigurationError
from hk_ipo_agent.common.settings import DatabaseSettings, Settings, _load_yaml


def test_database_url_async_psycopg() -> None:
    db = DatabaseSettings(
        host="db.example.com",
        port=5433,
        name="hki",
        user="alice",
        password=SecretStr("s3cr3t"),
    )
    assert db.url == "postgresql+asyncpg://alice:s3cr3t@db.example.com:5433/hki"
    assert db.sync_url == "postgresql+psycopg://alice:s3cr3t@db.example.com:5433/hki"


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
