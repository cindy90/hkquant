"""R2-5 add cornerstone_tracking_unreliable column to prediction_outcomes

Revision ID: r2_5_unreliable
Revises: 70d3eb3b7f96
Create Date: 2026-05-17 12:00:00+00:00

CLAUDE.md «基石减持检测的不确定性必须显式标注 tracking_unreliable=true»
was previously prose-only; this migration adds the column so the
schema-level contract is enforceable. Default is FALSE for back-compat:
existing prediction_outcomes rows read as tracking-reliable (matching
pre-R2-5 behaviour).

See docs/PLAN_post_v1.0.md §4 R2-5.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r2_5_unreliable"
down_revision: str | None = "70d3eb3b7f96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prediction_outcomes",
        sa.Column(
            "cornerstone_tracking_unreliable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("prediction_outcomes", "cornerstone_tracking_unreliable")
