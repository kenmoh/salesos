"""Add pending plan to merchant subscriptions.

Plan changes take effect at the start of the next 30-day billing period (no
proration), so the selected plan is staged in ``pending_plan_id`` until the
period rolls over.

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7g8h9i0
Create Date: 2026-11-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "e6f7g8h9i0j1"
down_revision: Union[str, None] = "d5e6f7g8h9i0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            ALTER TABLE merchant_subscriptions
            ADD COLUMN IF NOT EXISTS pending_plan_id UUID
                REFERENCES subscription_plans(id) ON DELETE SET NULL
            """
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE merchant_subscriptions
            ADD COLUMN IF NOT EXISTS plan_change_requested_at TIMESTAMPTZ
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE merchant_subscriptions DROP COLUMN IF EXISTS pending_plan_id"))
    conn.execute(
        text("ALTER TABLE merchant_subscriptions DROP COLUMN IF EXISTS plan_change_requested_at")
    )
