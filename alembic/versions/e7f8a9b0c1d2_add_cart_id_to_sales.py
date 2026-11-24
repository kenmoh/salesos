"""Add cart_id to sales table

Revision ID: e7f8a9b0c1d2
Revises: d0c1u2m3e4n5
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "e7f8a9b0c1d2"
down_revision = "d0c1u2m3e4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales",
        sa.Column(
            "cart_id",
            PG_UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sales", "cart_id")
