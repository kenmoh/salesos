"""Add show_bank_details toggle to documents

Revision ID: d0c1u2m3e4n5
Revises: t3n4a5n6t7f8
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "d0c1u2m3e4n5"
down_revision = "t3n4a5n6t7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "show_bank_details",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "show_bank_details")
