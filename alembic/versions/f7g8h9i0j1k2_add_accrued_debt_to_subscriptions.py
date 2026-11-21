"""Add accrued debt and debt cap to merchant subscriptions.

Transaction fees are now deducted from the tenant wallet immediately at sale
time. When the wallet cannot cover the full fee, the remainder accrues as
debt up to a configurable cap (default ₦2,000). Once debt reaches the cap,
new sales and document creation are blocked (402) until the balance is cleared.

Revision ID: f7g8h9i0j1k2
Revises: e6f7g8h9i0j1
Create Date: 2026-11-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "f7g8h9i0j1k2"
down_revision: Union[str, None] = "e6f7g8h9i0j1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            ALTER TABLE merchant_subscriptions
            ADD COLUMN IF NOT EXISTS accrued_debt NUMERIC(12,2) NOT NULL DEFAULT 0
            """
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE merchant_subscriptions
            ADD COLUMN IF NOT EXISTS debt_cap NUMERIC(12,2) NOT NULL DEFAULT 2000
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE merchant_subscriptions DROP COLUMN IF EXISTS accrued_debt"))
    conn.execute(text("ALTER TABLE merchant_subscriptions DROP COLUMN IF EXISTS debt_cap"))
