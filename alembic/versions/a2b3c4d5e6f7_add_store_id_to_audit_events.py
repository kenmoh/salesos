"""Add store_id to audit_events for store-scoped activity filtering.

Revision ID: a2b3c4d5e6f7
Revises: z9y8x7w6v5u4
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a2b3c4d5e6f7"
down_revision = "z9y8x7w6v5u4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "store_id")
