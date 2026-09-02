"""Add auto_create_cart flag to users

Revision ID: g6h7i8j9k0l1
Revises: f4a5b6c7d8e9
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa

revision = "g6h7i8j9k0l1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auto_create_cart",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "auto_create_cart")
