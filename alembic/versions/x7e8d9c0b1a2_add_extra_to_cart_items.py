"""Add extra override column to cart_items.

Revision ID: x7e8d9c0b1a2
Revises: z9a8b7c6d5e4
"""
from alembic import op

revision = "x7e8d9c0b1a2"
down_revision = "z9a8b7c6d5e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE cart_items ADD COLUMN extra NUMERIC(15, 2) NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE cart_items DROP COLUMN extra")
