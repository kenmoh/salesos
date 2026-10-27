"""Contact messages inbox for the website form.

The table is deliberately outside the tenancy model and RLS: submissions
come from anonymous visitors of the marketing site, and nothing in it keys
on a business. Platform admins read it through the platform router.

Revision ID: b2d3e4f5a6b7
Revises: b0c1d2e3f4a5
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa

revision = "b2d3e4f5a6b7"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contact_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("business_name", sa.String(length=120), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contact_messages_email"), "contact_messages", ["email"])
    # No RLS policy on purpose: anonymous visitors write, platform admins read.


def downgrade():
    op.drop_index(op.f("ix_contact_messages_email"), table_name="contact_messages")
    op.drop_table("contact_messages")
