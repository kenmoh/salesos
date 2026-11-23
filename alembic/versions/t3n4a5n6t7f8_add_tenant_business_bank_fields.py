"""Add tenant business description and settlement bank fields

Revision ID: t3n4a5n6t7f8
Revises: m2n3o4p5q6r7s8
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "t3n4a5n6t7f8"
down_revision = "m2n3o4p5q6r7s8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("business_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("account_number", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("account_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("bank_name", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "bank_name")
    op.drop_column("tenants", "account_name")
    op.drop_column("tenants", "account_number")
    op.drop_column("tenants", "business_description")
