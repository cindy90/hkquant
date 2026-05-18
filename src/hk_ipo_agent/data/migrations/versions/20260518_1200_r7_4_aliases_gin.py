"""R7-4 add GIN index on cornerstone_investors.aliases for JSONB lookup

Revision ID: r7_4_aliases_gin
Revises: r2_5_unreliable
Create Date: 2026-05-18 12:00:00+00:00

Pre-R7-4 the only name-resolution path for cornerstone investors hit
``name_zh`` / ``name_en`` exact equality. Now ``find_by_any_alias()``
queries ``aliases['items'] @> [{"text": name}]`` against the JSONB
column. Without a GIN index that's an O(n) scan over all 1,314
investor rows on every prospectus extraction.

This migration installs ``ix_cornerstone_investors_aliases_gin`` so the
containment lookup is index-backed.

See docs/PLAN_post_v1.0.md §R7-4.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r7_4_aliases_gin"
down_revision: str | None = "r2_5_unreliable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use raw SQL because Alembic's ``create_index`` lacks first-class GIN
    # support across dialect versions. ``IF NOT EXISTS`` is safe-by-default
    # so re-running the migration is a no-op.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_cornerstone_investors_aliases_gin "
            "ON cornerstone_investors USING gin (aliases jsonb_path_ops)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cornerstone_investors_aliases_gin"))
