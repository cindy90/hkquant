"""Timeout / silent-expiry detection — PROJECT_SPEC.md §3.11.1.

Per CLAUDE.md v1.2 "超时不等于失败. stale_detector 触发的是警报而非
自动 WITHDRAWN" — this module only emits ``StaleSignal``s; the caller
(daily scheduler in 7.5d) decides whether to route to alerts or open
a manual-review task.

Two staleness thresholds, fixed by spec §3.11.1:
- PRE_LISTING > 180 days  → prospectus 6-month validity has lapsed
- PRICING     > 21 days   → pricing window normally 1-2 weeks
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ...common.enums import AlertLevel, IPOLifecycleStateType
from ...data.models import IPOLifecycleStateRow

PRE_LISTING_STALE_DAYS = 180
PRICING_STALE_DAYS = 21


@dataclass(frozen=True)
class StaleSignal:
    """A single staleness finding. Multiple may fire per scheduler run."""

    ipo_id: UUID
    state: IPOLifecycleStateType
    days_in_state: int
    threshold_days: int
    severity: AlertLevel
    message: str
    actionable_info: str


class StaleDetector:
    """Scans active (non-terminal) lifecycle rows for stale ones."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def scan(self, *, as_of: datetime | None = None) -> list[StaleSignal]:
        """Returns a (possibly empty) list of staleness signals."""
        anchor = as_of or datetime.now(UTC)
        stmt = select(IPOLifecycleStateRow).where(
            IPOLifecycleStateRow.is_terminal.is_(False)
        )
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()

        signals: list[StaleSignal] = []
        for row in rows:
            sig = self._signal_for_row(row, anchor=anchor)
            if sig is not None:
                signals.append(sig)
        return signals

    @staticmethod
    def _signal_for_row(
        row: IPOLifecycleStateRow,
        *,
        anchor: datetime,
    ) -> StaleSignal | None:
        state = IPOLifecycleStateType(row.current_state)
        delta_days = (anchor - row.state_entered_at).days

        if state is IPOLifecycleStateType.PRE_LISTING and delta_days > PRE_LISTING_STALE_DAYS:
            return StaleSignal(
                ipo_id=row.ipo_id,
                state=state,
                days_in_state=delta_days,
                threshold_days=PRE_LISTING_STALE_DAYS,
                severity=AlertLevel.CRITICAL,
                message=(
                    f"IPO {row.ipo_id} 在 PRE_LISTING 状态停留 {delta_days} 天 "
                    f"(> {PRE_LISTING_STALE_DAYS}d). 招股书 6 个月有效期可能已失效."
                ),
                actionable_info=(
                    "请人工核实：IPO 是否实际已撤回但 HKEX 未发公告？"
                    "若已失效，建议手动 transition 到 WITHDRAWN 并补做 terminal review."
                ),
            )
        if state is IPOLifecycleStateType.PRICING and delta_days > PRICING_STALE_DAYS:
            return StaleSignal(
                ipo_id=row.ipo_id,
                state=state,
                days_in_state=delta_days,
                threshold_days=PRICING_STALE_DAYS,
                severity=AlertLevel.WARNING,
                message=(
                    f"IPO {row.ipo_id} 在 PRICING 状态停留 {delta_days} 天 "
                    f"(> {PRICING_STALE_DAYS}d). 招股期通常 1-2 周."
                ),
                actionable_info=(
                    "请人工核实：定价是否已发生但 detect_listed 未触发？"
                    "或公司是否已取消发行（应转 PRICING_PULLED）？"
                ),
            )
        return None


def days_in_state(
    entered_at: datetime, *, as_of: datetime | None = None
) -> int:
    """Public helper — used by the daily scheduler diagnostic dashboard."""
    anchor = as_of or datetime.now(UTC)
    return (anchor - entered_at).days


# Suppress unused-import lint when timedelta isn't used by tests below.
_ = timedelta


__all__ = (
    "PRE_LISTING_STALE_DAYS",
    "PRICING_STALE_DAYS",
    "StaleDetector",
    "StaleSignal",
    "days_in_state",
)
