"""Add numeric limit_value to plan_features for quota enforcement.

Quotas read this integer rather than parsing the human-readable
feature_value strings ("100 products" etc.). Backfills the products cap
for the seeded plans: 100 / 1000 / 10000.

Revision ID: l2m3n4o5p6q7r8
Revises: k1l2m3n4o5p6q7
Create Date: 2026-11-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "l2m3n4o5p6q7r8"
down_revision: Union[str, None] = "k1l2m3n4o5p6q7"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("plan_features", sa.Column("limit_value", sa.Integer(), nullable=True))
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE plan_features
            SET limit_value = REPLACE(SPLIT_PART(feature_value, ' ', 1), ',', '')::int
            WHERE feature_key = 'products'
              AND feature_value IS NOT NULL
              AND REPLACE(SPLIT_PART(feature_value, ' ', 1), ',', '') ~ '^[0-9]+$'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("plan_features", "limit_value")
