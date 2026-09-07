"""Add user OAuth provider fields

Revision ID: m1n2o3p4q5r6
Revises: k1l2m3n4o5p6
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "m1n2o3p4q5r6"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("provider", sa.String(30), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("provider_id", sa.String(255), nullable=True),
    )
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(255),
        nullable=True,
    )
    op.create_index("ix_users_provider", "users", ["provider"])
    op.create_index("ix_users_provider_id", "users", ["provider_id"])


def downgrade() -> None:
    op.drop_index("ix_users_provider_id", table_name="users")
    op.drop_index("ix_users_provider", table_name="users")
    op.drop_column("users", "provider_id")
    op.drop_column("users", "provider")
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(255),
        nullable=False,
    )
