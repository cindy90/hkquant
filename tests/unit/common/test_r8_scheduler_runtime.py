"""R8-5 — SchedulerSettings.backend is Literal-typed; prod must use airflow.

Pre-R8-5 ``SchedulerSettings.backend`` was a free-form ``str``. A typo
("apsheduler", "airfllow") would silently fall through to whatever
branch handled the unrecognised value — likely the apscheduler dev
branch in production. CLAUDE.md §自动化与状态机约束: "生产环境必须用
Airflow。APScheduler 仅用于 dev/test."

Post-R8-5:
  * ``backend: Literal["airflow", "apscheduler"]`` — typos rejected at
    Settings construction time.
  * A ``model_validator(after)`` raises ``ConfigurationError`` when
    ``environment`` is prod/production AND ``backend != "airflow"``.
"""

from __future__ import annotations

import pytest

from hk_ipo_agent.common.exceptions import ConfigurationError
from hk_ipo_agent.common.settings import SchedulerSettings, Settings, get_settings


def test_scheduler_backend_is_literal_typed() -> None:
    """R8-5 — typed annotation rejects unknown strings."""
    # apscheduler / airflow accepted
    SchedulerSettings(backend="apscheduler")
    SchedulerSettings(backend="airflow")

    # Typo rejected by pydantic.
    with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError or similar
        SchedulerSettings(backend="apsheduler")  # type: ignore[arg-type]
    with pytest.raises(Exception):  # noqa: B017
        SchedulerSettings(backend="celery")  # type: ignore[arg-type]


def test_prod_requires_airflow_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8-5 — production environment MUST use airflow.

    The model_validator runs at Settings construction. We use the
    direct ``Settings()`` form (not from_yaml_and_env, which layers
    YAML defaults on top of env). Other prod-gate env vars are set so
    we don't trip R2-1 / R2-7 before hitting R8-5.
    """
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "prod")
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")
    monkeypatch.setenv("HK_IPO__AUTH__JWT_SECRET", "prod-secret-min-32-chars-long-enough-1")
    monkeypatch.setenv("HK_IPO__SCHEDULER__BACKEND", "apscheduler")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError, match="airflow"):
        Settings()


def test_prod_with_airflow_backend_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8-5 — happy path: production + airflow → no raise."""
    monkeypatch.setenv("HK_IPO__ENVIRONMENT", "prod")
    monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")
    monkeypatch.setenv("HK_IPO__AUTH__JWT_SECRET", "prod-secret-min-32-chars-long-enough-1")
    monkeypatch.setenv("HK_IPO__SCHEDULER__BACKEND", "airflow")
    get_settings.cache_clear()

    s = Settings()
    assert s.scheduler.backend == "airflow"
    assert s.environment.lower() in {"prod", "production"}


def test_dev_with_apscheduler_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8-5 — dev environment + apscheduler → no raise (default path)."""
    # No env overrides → defaults environment=dev, backend=apscheduler.
    get_settings.cache_clear()
    s = Settings()
    assert s.scheduler.backend == "apscheduler"
