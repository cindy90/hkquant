"""Alert routing per PROJECT_SPEC.md §3.11.

Three severity tiers with strictly different routing:
- info    → log only
- warning → 24-hour review queue (de-duped on category + ipo_id)
- critical → immediate notification (Email / Slack / PagerDuty / SMS)

CLAUDE.md v1.2 constraints:
- "所有警报必须含 actionable_info 字段" — emit() validates non-empty
- "自动去重：同 (category, ipo_id, level) 在 24h 内只发一次" — uses
  alerts table with detected_at; checks last 24h before INSERT

Routing config lives in ``config/alerts.yaml``; the module loads it on
construction and falls back to log-only routing when missing.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..common.enums import AlertLevel
from ..common.exceptions import HkIpoAgentException
from ..common.logging import get_logger
from ..common.schemas import Alert
from ..data.models import AlertRow

logger = get_logger(__name__)

DEDUP_WINDOW = timedelta(hours=24)
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "alerts.yaml"


class AlertConfigError(HkIpoAgentException):
    """Raised on missing / malformed alerts.yaml."""

    default_message = "Alert configuration error"


def load_alerts_config(path: Path | None = None) -> dict[str, Any]:
    """Parse alerts.yaml. Returns ``{}`` if missing (defaults to log-only)."""
    target = path or _DEFAULT_CONFIG_PATH
    if not target.exists():
        logger.warning("alerts_config_not_found", path=str(target))
        return {}
    try:
        return yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AlertConfigError(f"failed to parse {target}: {exc}") from exc


class AlertRouter:
    """Deduplicating alert sink. Construct once per process."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        config: dict[str, Any] | None = None,
    ) -> None:
        self._sf = session_factory
        self._config = config if config is not None else load_alerts_config()

    async def emit(
        self,
        *,
        level: AlertLevel,
        category: str,
        message: str,
        actionable_info: str,
        related_ipo_id: UUID | None = None,
        related_snapshot_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Alert | None:
        """Emit an alert (subject to 24h dedup). Returns None when suppressed.

        Raises ``ValueError`` if ``actionable_info`` is empty (CLAUDE.md v1.2).
        """
        if not actionable_info or not actionable_info.strip():
            raise ValueError(
                "Alert emit refused: 'actionable_info' is required and non-empty "
                "per CLAUDE.md v1.2 ('alerts must say what to do, not just \"failed\"')."
            )

        if await self._is_duplicate(level, category, related_ipo_id):
            logger.debug(
                "alert_suppressed_dedup",
                level=level.value,
                category=category,
                related_ipo_id=str(related_ipo_id) if related_ipo_id else None,
            )
            return None

        now = datetime.now(UTC)
        alert = Alert(
            level=level,
            category=category,
            related_ipo_id=related_ipo_id,
            related_snapshot_id=related_snapshot_id,
            message=message,
            actionable_info=actionable_info,
            detected_at=now,
            metadata=metadata or {},
        )
        await self._persist(alert)
        await self._route(alert)
        return alert

    async def _is_duplicate(
        self,
        level: AlertLevel,
        category: str,
        ipo_id: UUID | None,
    ) -> bool:
        """24-hour dedup on (category, ipo_id, level)."""
        cutoff = datetime.now(UTC) - DEDUP_WINDOW
        stmt = (
            select(AlertRow.id)
            .where(AlertRow.level == level.value)
            .where(AlertRow.category == category)
            .where(AlertRow.detected_at >= cutoff)
            .limit(1)
        )
        if ipo_id is not None:
            stmt = stmt.where(AlertRow.related_ipo_id == ipo_id)
        else:
            stmt = stmt.where(AlertRow.related_ipo_id.is_(None))
        async with self._sf() as s:
            return (await s.execute(stmt)).scalar_one_or_none() is not None

    async def _persist(self, alert: Alert) -> UUID:
        row = AlertRow(
            id=_uuid.uuid4(),
            level=alert.level.value,
            category=alert.category,
            related_ipo_id=alert.related_ipo_id,
            related_snapshot_id=alert.related_snapshot_id,
            message=alert.message,
            actionable_info=alert.actionable_info,
            detected_at=alert.detected_at,
            extra_metadata=alert.metadata,
        )
        async with self._sf() as s:
            s.add(row)
            await s.commit()
        return row.id

    async def _route(self, alert: Alert) -> None:
        """Send the alert via the configured channels for its level.

        MVP: routes are logged. Production lifespan wires real Slack /
        PagerDuty / Email clients via the same interface.
        """
        channels = self._config.get("levels", {}).get(alert.level.value, [])
        if not channels:
            logger.info(
                "alert_routed_log_only",
                level=alert.level.value,
                category=alert.category,
                message=alert.message[:200],
            )
            return
        for channel in channels:
            logger.info(
                "alert_routed",
                level=alert.level.value,
                channel=channel,
                category=alert.category,
                message=alert.message[:200],
                actionable_info=alert.actionable_info[:200],
            )

    async def ack(self, alert_id: UUID, *, ack_by: str) -> bool:
        """Acknowledge an open alert. Returns True iff the row existed + was open."""
        async with self._sf() as s:
            row = await s.get(AlertRow, alert_id)
            if row is None or row.acknowledged_at is not None:
                return False
            row.acknowledged_at = datetime.now(UTC)
            row.acknowledged_by = ack_by
            await s.commit()
        return True


__all__ = (
    "DEDUP_WINDOW",
    "AlertConfigError",
    "AlertRouter",
    "load_alerts_config",
)
