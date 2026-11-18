"""Add extra override column to sale_items.

Revision ID: y8f9e0d1c2b3
Revises: x7e8d9c0b1a2
"""
from alembic import op

revision = "y8f9e0d1c2b3"
down_revision = "x7e8d9c0b1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sale_items ADD COLUMN extra NUMERIC(15, 2) NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE sale_items DROP COLUMN extra")
