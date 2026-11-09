"""Feedback reports table for bug reports and feature requests.

Adds a single table with an enum field distinguishing bug reports from
feature requests. The table is tenant-scoped so each business only sees
its own feedback.

Revision ID: a1a2a3a4a5a6
Revises: b4c5d6e7f8a9
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

revision = "a1a2a3a4a5a6"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_feedback_reports_tenant_id"), "feedback_reports", ["tenant_id"]
    )
    op.create_index(op.f("ix_feedback_reports_user_id"), "feedback_reports", ["user_id"])


def downgrade():
    op.drop_index(op.f("ix_feedback_reports_user_id"), table_name="feedback_reports")
    op.drop_index(op.f("ix_feedback_reports_tenant_id"), table_name="feedback_reports")
    op.drop_table("feedback_reports")
