"""A+H dual-listing special handling — PROJECT_SPEC.md §3.11.1.

Three deltas from a standard mainboard IPO:

1. The A-share has been trading the entire pre-H window — that price
   series is itself a signal to be folded into the checkpoint-day +1
   baseline.
2. ``listing_date`` for outcome tracking = the H-share first day, NOT
   the A-share's earlier listing date.
3. Attribution must consider that the A-share period may have already
   priced in much of the public info — so a "miss" on a Bear concern
   that was already in the A-share price isn't a true miss.

This module is a tiny pure-function helper used by:
- ``outcome_tracker.py`` for resolving the correct listing date
- ``attribution.py`` (Phase 10 extension) for A-share-period
  discounting

We don't ship a full A-share price service here — Phase 4
``valuation/ah_premium.py`` already has the iFind A-share history
fetcher, which can be reused by Phase 10 attribution refinements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from ...common.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AHContext:
    """Resolved A+H pair info for one IPO.

    All fields are optional — a regular HK-only IPO returns an AHContext
    with ``is_ah_pair=False`` and the H-share fields populated only.
    """

    ipo_id: UUID
    is_ah_pair: bool
    h_share_code: str | None
    a_share_code: str | None
    h_listing_date: date | None
    a_listing_date: date | None

    @property
    def checkpoint_anchor_date(self) -> date | None:
        """Returns the date the T+N checkpoints should count from.

        Per spec §3.11.1: "checkpoint 计时从 H 股上市日开始".
        """
        return self.h_listing_date


class AHSpecialHandler:
    """Stateless helper for A+H lifecycle quirks."""

    @staticmethod
    def from_ipo_metadata(
        ipo_id: UUID,
        *,
        h_share_code: str | None,
        a_share_code: str | None,
        h_listing_date: date | None,
        a_listing_date: date | None,
    ) -> AHContext:
        """Build AHContext from already-resolved metadata."""
        is_ah = bool(h_share_code and a_share_code)
        return AHContext(
            ipo_id=ipo_id,
            is_ah_pair=is_ah,
            h_share_code=h_share_code,
            a_share_code=a_share_code,
            h_listing_date=h_listing_date,
            a_listing_date=a_listing_date,
        )

    @staticmethod
    def resolve_checkpoint_date(
        ah_context: AHContext,
        checkpoint_day: int,
    ) -> date | None:
        """Compute calendar date for a T+N checkpoint.

        Returns None if the H-share listing date isn't known yet (the
        scheduler should skip until ``code_mapper`` resolves it).
        """
        anchor = ah_context.checkpoint_anchor_date
        if anchor is None:
            return None
        from datetime import timedelta

        return anchor + timedelta(days=max(checkpoint_day, 0))

    @staticmethod
    def discount_pre_listing_signals(
        ah_context: AHContext,
        agent_findings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Tag findings as "potentially pre-discounted by A-share price action".

        Doesn't modify the findings; just annotates so attribution can
        weight them appropriately. Phase 10 learning loop will consume
        the ``pre_discounted_in_a_share`` tag.
        """
        if not ah_context.is_ah_pair:
            return agent_findings
        return [{**f, "pre_discounted_in_a_share": True} for f in agent_findings]


__all__ = (
    "AHContext",
    "AHSpecialHandler",
)
