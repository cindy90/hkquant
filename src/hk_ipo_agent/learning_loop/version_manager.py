"""Config / prompt version manager — Phase 10a per ADR 0015.

Maintains the history of ``config/*.yaml`` and ``prompts/*.md`` files
in the ``config_versions`` PG table (schema landed in Phase 7.5a).

Every Phase 10 ``adjustment_applier`` modification routes through this
module:

1. ``bump_version(target_path, new_content, applied_by, source_review_id)``
   — writes a new row + computes the next semver (patch bump by default).
2. ``get_active_version(target_path)`` — returns the latest version
   row for a path.
3. ``rollback(target_path, target_version, applied_by)`` — creates a
   *new* row whose content matches ``target_version`` but with
   ``change_type="rollback"``. We never delete history.

CLAUDE.md prediction-lifecycle binding: every snapshot must be able to
locate the exact config / prompt versions in effect at its creation
time — so we never overwrite or delete previous rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, text

from ..common.logging import get_logger

logger = get_logger(__name__)


SEMVER_PATCH_DELIMITER: str = "."
DEFAULT_SEED_VERSION: str = "1.0.0"


@dataclass(frozen=True)
class ConfigVersion:
    """Pure-Python projection of a ``config_versions`` row."""

    id: UUID
    target_path: str
    version: str
    content_hash: str | None
    content: dict[str, Any] | None
    change_type: str
    source_review_id: UUID | None
    applied_by: str | None
    applied_at: datetime


# ===========================================================================
# Semver helpers
# ===========================================================================


def bump_semver_patch(version: str) -> str:
    """1.0.3 → 1.0.4. Fails closed: invalid input → '1.0.0'."""
    parts = version.split(SEMVER_PATCH_DELIMITER)
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return DEFAULT_SEED_VERSION
    major, minor, patch = (int(p) for p in parts)
    return f"{major}.{minor}.{patch + 1}"


def hash_content(content: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON — used as content_hash for de-dup."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _advisory_lock_key(target_path: str) -> int:
    """R3-6 — derive a stable signed 64-bit advisory lock key from path.

    PG ``pg_advisory_xact_lock(bigint)`` takes a signed 64-bit integer.
    We use the high 8 bytes of SHA-256(target_path) and coerce to the
    signed range. Same target_path → same lock slot, different paths
    → effectively non-colliding (birthday bound ≈ 2^32 entries).
    """
    digest = hashlib.sha256(target_path.encode("utf-8")).digest()[:8]
    # Treat as signed 64-bit (PG bigint).
    return int.from_bytes(digest, byteorder="big", signed=True)


# ===========================================================================
# Version manager
# ===========================================================================


class VersionManager:
    """Async-friendly version manager backed by ``config_versions`` PG table.

    Construct with a session_factory; methods open their own session for
    each call to avoid lock-holding across LLM / agent boundaries.
    """

    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def get_active_version(self, target_path: str) -> ConfigVersion | None:
        """Latest row for ``target_path`` by ``applied_at`` desc."""
        from ..data.models import ConfigVersionRow

        async with self._sf() as s:
            stmt = (
                select(ConfigVersionRow)
                .where(ConfigVersionRow.target_path == target_path)
                .order_by(desc(ConfigVersionRow.applied_at))
                .limit(1)
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
        return _row_to_dataclass(row) if row is not None else None

    async def list_versions(
        self,
        target_path: str,
        *,
        limit: int = 50,
    ) -> list[ConfigVersion]:
        """All versions for a path, newest-first."""
        from ..data.models import ConfigVersionRow

        async with self._sf() as s:
            stmt = (
                select(ConfigVersionRow)
                .where(ConfigVersionRow.target_path == target_path)
                .order_by(desc(ConfigVersionRow.applied_at))
                .limit(limit)
            )
            rows = list((await s.execute(stmt)).scalars().all())
        return [_row_to_dataclass(r) for r in rows]

    async def bump_version(
        self,
        target_path: str,
        new_content: dict[str, Any],
        *,
        applied_by: str | None = None,
        source_review_id: UUID | None = None,
        change_type: str = "learning_loop_applied",
    ) -> ConfigVersion:
        """Write a new ``config_versions`` row with auto-bumped patch semver.

        R3-6: read-current + compute-next + insert are now serialised on a
        per-target_path advisory lock so two concurrent appliers can't both
        compute "1.0.1" and have one trip the UNIQUE constraint. The lock
        also covers the read so the second caller sees the first one's
        commit and bumps to 1.0.2 instead of 1.0.1.

        Args:
            target_path: e.g. ``config/valuation_weights.yaml``.
            new_content: the full new content as a dict.
            applied_by: 'system' / reviewer email / 'manual:cli'.
            source_review_id: the review whose proposal triggered this.
            change_type: 'manual' / 'learning_loop_applied' / 'rollback'.

        Returns:
            The newly-written ``ConfigVersion``.
        """
        from ..data.models import ConfigVersionRow

        # R3-6 — derive a stable 64-bit lock key from target_path.
        # pg_advisory_xact_lock takes a bigint; we use the high 8 bytes of
        # sha256 to give us a unique, non-colliding slot per target_path.
        lock_key = _advisory_lock_key(target_path)
        content_hash = hash_content(new_content)

        async with self._sf() as s:
            # Take the transactional advisory lock. Released automatically
            # at commit/rollback. Blocks (within the same PG cluster) any
            # other connection that asks for the same lock_key.
            await s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

            # Read CURRENT in the SAME transaction as the lock + INSERT.
            stmt = (
                select(ConfigVersionRow)
                .where(ConfigVersionRow.target_path == target_path)
                .order_by(desc(ConfigVersionRow.applied_at))
                .limit(1)
            )
            current_row = (await s.execute(stmt)).scalar_one_or_none()
            new_version = (
                bump_semver_patch(current_row.version)
                if current_row is not None
                else DEFAULT_SEED_VERSION
            )

            row = ConfigVersionRow(
                target_path=target_path,
                version=new_version,
                content_hash=content_hash,
                content=new_content,
                change_type=change_type,
                source_review_id=source_review_id,
                applied_by=applied_by,
                applied_at=datetime.now(UTC),
            )
            s.add(row)
            await s.commit()  # releases advisory lock
            await s.refresh(row)
        logger.info(
            "config_version_bumped",
            target=target_path,
            version=new_version,
            change_type=change_type,
        )
        return _row_to_dataclass(row)

    async def rollback(
        self,
        target_path: str,
        target_version: str,
        *,
        applied_by: str | None = None,
    ) -> ConfigVersion:
        """Roll back: create a NEW row whose content matches ``target_version``.

        We never modify or delete the version history; rollback is just
        a forward-write that re-instates older content as the active row.
        """
        from ..data.models import ConfigVersionRow

        async with self._sf() as s:
            stmt = select(ConfigVersionRow).where(
                ConfigVersionRow.target_path == target_path,
                ConfigVersionRow.version == target_version,
            )
            target_row = (await s.execute(stmt)).scalar_one_or_none()
        if target_row is None:
            raise KeyError(f"version {target_version} for {target_path} not found")
        # Bump-as-rollback.
        return await self.bump_version(
            target_path,
            target_row.content or {},
            applied_by=applied_by,
            source_review_id=None,
            change_type="rollback",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dataclass(row: Any) -> ConfigVersion:
    return ConfigVersion(
        id=row.id,
        target_path=row.target_path,
        version=row.version,
        content_hash=row.content_hash,
        content=row.content,
        change_type=row.change_type or "unknown",
        source_review_id=row.source_review_id,
        applied_by=row.applied_by,
        applied_at=row.applied_at,
    )


__all__ = (
    "DEFAULT_SEED_VERSION",
    "ConfigVersion",
    "VersionManager",
    "bump_semver_patch",
    "hash_content",
)
