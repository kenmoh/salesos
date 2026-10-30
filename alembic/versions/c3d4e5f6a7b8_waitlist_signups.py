"""Waitlist signups collected from the landing page form.

The table is deliberately outside the tenancy model and RLS: signups come
from anonymous visitors of the marketing site, and nothing in it keys on a
business. Platform admins read it through the platform router.

Revision ID: c3d4e5f6a7b8
Revises: 9c5d6e7f8a10
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "9c5d6e7f8a10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "waitlist_signups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("business_name", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_waitlist_signups_email"), "waitlist_signups", ["email"], unique=True)
    # No RLS policy on purpose: anonymous visitors write, platform admins read.


def downgrade():
    op.drop_index(op.f("ix_waitlist_signups_email"), table_name="waitlist_signups")
    op.drop_table("waitlist_signups")
