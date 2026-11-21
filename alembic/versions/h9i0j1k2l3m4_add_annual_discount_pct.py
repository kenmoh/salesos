"""Add annual_discount_pct to subscription_plans.

Per-plan annual billing discount percentage (default 17%). Configurable per
plan so admins can tune the annual discount for each tier.

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-11-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "h9i0j1k2l3m4"
down_revision: Union[str, None] = "g8h9i0j1k2l3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            ALTER TABLE subscription_plans
            ADD COLUMN IF NOT EXISTS annual_discount_pct NUMERIC(5,2) NOT NULL DEFAULT 17.00
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text("ALTER TABLE subscription_plans DROP COLUMN IF EXISTS annual_discount_pct")
    )
