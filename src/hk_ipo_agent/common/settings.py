"""Layered configuration loader per PROJECT_SPEC.md §3.3.

Precedence (highest wins):
    1. Environment variables (HK_IPO__*)
    2. .env file
    3. config/settings.yaml (and other YAML in config/)
    4. Pydantic field defaults

Usage:
    from hk_ipo_agent.common.settings import get_settings
    s = get_settings()
    print(s.database.url)
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

# Resolve config dir relative to repo root (this file lives at src/hk_ipo_agent/common/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _REPO_ROOT / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dict (empty dict if file missing or empty)."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"YAML at {path} must be a mapping at top level, got {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings."""

    host: str = "localhost"
    port: int = 5432
    name: str = "hkipo"
    user: str = "hkipo"
    password: SecretStr = SecretStr("hkipo")
    pool_size: int = 10

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class QdrantSettings(BaseSettings):
    url: str = "http://localhost:6333"
    api_key: SecretStr | None = None


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379/0"


class LLMSettings(BaseSettings):
    anthropic_api_key: SecretStr = SecretStr("")
    max_retries: int = 3
    timeout_seconds: int = 120
    cost_daily_budget_usd: float = 20.0


class ProspectusSettings(BaseSettings):
    llama_cloud_api_key: SecretStr | None = None
    parser_max_pages: int = 800


class EmbeddingSettings(BaseSettings):
    provider: str = "local"  # local | voyage
    bge_model_path: str = "BAAI/bge-large-zh-v1.5"
    voyage_api_key: SecretStr | None = None


class IFindSettings(BaseSettings):
    username: str = ""
    password: SecretStr = SecretStr("")
    qps_limit: int = 10


class APISettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    rate_limit_per_min: int = 60


class AuthSettings(BaseSettings):
    jwt_secret: SecretStr = SecretStr("change-me-min-32-chars-long-secret-here")
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_seconds: int = 3600
    jwt_refresh_token_ttl_seconds: int = 604800
    sso_provider: str = "local"
    sso_issuer_url: str = ""
    sso_client_id: str = ""
    sso_client_secret: SecretStr = SecretStr("")
    sso_redirect_uri: str = ""


class SchedulerSettings(BaseSettings):
    backend: str = "apscheduler"  # apscheduler (dev) | airflow (prod)
    timezone: str = "Asia/Hong_Kong"


# ---------------------------------------------------------------------------
# Top-level settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Top-level project settings.

    Override values via:
      - Env vars with prefix HK_IPO__ (e.g. HK_IPO__DATABASE__HOST=...)
      - .env file at repo root
      - config/*.yaml (loaded from `_yaml_overrides`)
    """

    model_config = SettingsConfigDict(
        env_prefix="HK_IPO__",
        env_nested_delimiter="__",
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    data_dir: Path = _REPO_ROOT / "data"
    output_dir: Path = _REPO_ROOT / "outputs"

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    prospectus: ProspectusSettings = Field(default_factory=ProspectusSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    ifind: IFindSettings = Field(default_factory=IFindSettings)
    api: APISettings = Field(default_factory=APISettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)

    @classmethod
    def from_yaml_and_env(cls) -> Settings:
        """Build settings by layering YAML overrides under env / .env precedence."""
        yaml_data = _load_yaml(_CONFIG_DIR / "settings.yaml")
        # The YAML file uses short top-level keys (`environment`, `log_level`, etc.) per
        # config/settings.yaml. Map them as default overrides on top of field defaults.
        # Env vars and .env still take precedence (higher in pydantic-settings order).
        return cls(**yaml_data)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached)."""
    return Settings.from_yaml_and_env()


# ---------------------------------------------------------------------------
# Auxiliary YAML loaders (per-subsystem configs not in Settings tree)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_llm_models_config() -> dict[str, Any]:
    """Read ``config/llm_models.yaml`` — LLM routing per agent / extraction task.

    Consumed by ``common/llm_client.py`` and the agent layer (Phase 5+) to
    determine which Claude model + token caps to use for each role.
    """
    return _load_yaml(_CONFIG_DIR / "llm_models.yaml")


@functools.lru_cache(maxsize=1)
def load_data_sources_config() -> dict[str, Any]:
    """Read ``config/data_sources.yaml`` — endpoints + rate limits."""
    return _load_yaml(_CONFIG_DIR / "data_sources.yaml")


@functools.lru_cache(maxsize=1)
def load_agents_config() -> dict[str, Any]:
    """Read ``config/agents.yaml`` — per-agent runtime config (Phase 5+)."""
    return _load_yaml(_CONFIG_DIR / "agents.yaml")


@functools.lru_cache(maxsize=1)
def load_valuation_weights_config() -> dict[str, Any]:
    """Read ``config/valuation_weights.yaml`` — ensemble weights by ListingType."""
    return _load_yaml(_CONFIG_DIR / "valuation_weights.yaml")


def load_regulations_config(filename: str) -> dict[str, Any]:
    """Read one file from ``config/regulations/`` (e.g. ``ipo_rules_post_20250804.yaml``).

    Not cached because Phase 5 PolicyAgent may dispatch by as_of_date and a
    given run can switch between regime files; caching per-file would be premature.
    """
    return _load_yaml(_CONFIG_DIR / "regulations" / filename)


def clear_config_caches() -> None:
    """Reset all config caches. Used by tests to force re-reads after monkeypatching YAML files."""
    get_settings.cache_clear()
    load_llm_models_config.cache_clear()
    load_data_sources_config.cache_clear()
    load_agents_config.cache_clear()
    load_valuation_weights_config.cache_clear()


__all__ = (
    "APISettings",
    "AuthSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "IFindSettings",
    "LLMSettings",
    "ProspectusSettings",
    "QdrantSettings",
    "RedisSettings",
    "SchedulerSettings",
    "Settings",
    "clear_config_caches",
    "get_settings",
    "load_agents_config",
    "load_data_sources_config",
    "load_llm_models_config",
    "load_regulations_config",
    "load_valuation_weights_config",
)
