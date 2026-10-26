"""Add cash_amount to platform_commissions — the platform-only rate.

The full `amount` bundles the external processor's share (2% gateway) on top
of the platform's commission (1.5%), but collections that arrive without a
processor — cash at the counter, a converted document, a standalone receipt —
were billed the processor's share too. `cash_amount` holds the processorless
rate; NULL keeps the old behaviour until the platform sets it.

Revision ID: b0c1d2e3f4a5
Revises: a9c4e7f2b6d1
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa

revision = "b0c1d2e3f4a5"
down_revision = "a9c4e7f2b6d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_commissions",
        sa.Column("cash_amount", sa.Numeric(12, 2), nullable=True),
    )
    # Seed the platform-only share for rules already at the full 3.5%
    # (2% processor + 1.5% platform). Rules at any other rate keep NULL and
    # continue to charge the full amount until set explicitly.
    op.execute("UPDATE platform_commissions SET cash_amount = 1.5 WHERE amount = 3.5")

    # Fresh environments have no rule at all, and without one no fee is ever
    # booked. Insert the standard rule only when the table is empty, so a
    # rule already created through the platform admin API is left alone.
    op.execute(
        """
        INSERT INTO platform_commissions
            (id, label, fee_type, amount, cash_amount, min_threshold,
             max_pending_balance, created_at, updated_at)
        SELECT gen_random_uuid(), 'standard', 'percentage', 3.5, 1.5, 0, 1000,
               now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM platform_commissions)
        """
    )


def downgrade():
    op.drop_column("platform_commissions", "cash_amount")
