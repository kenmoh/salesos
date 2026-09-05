"""add intent-first checkout fields to payment_intents

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make sale_id nullable (was NOT NULL, now optional for intent-first flow)
    op.alter_column(
        "payment_intents",
        "sale_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # Add cart snapshot fields
    op.add_column(
        "payment_intents",
        sa.Column("cart_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True, index=True),
    )
    op.add_column(
        "payment_intents",
        sa.Column("customer_name", sa.String(200), nullable=True),
    )
    op.add_column(
        "payment_intents",
        sa.Column("customer_phone", sa.String(50), nullable=True),
    )
    op.add_column(
        "payment_intents",
        sa.Column("coupon_code", sa.String(50), nullable=True),
    )
    op.add_column(
        "payment_intents",
        sa.Column("cart_snapshot", sa.Text, nullable=True, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("payment_intents", "cart_snapshot")
    op.drop_column("payment_intents", "coupon_code")
    op.drop_column("payment_intents", "customer_phone")
    op.drop_column("payment_intents", "customer_name")
    op.drop_column("payment_intents", "cart_id")
    op.alter_column(
        "payment_intents",
        "sale_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
