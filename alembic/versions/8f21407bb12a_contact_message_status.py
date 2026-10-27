"""contact message status

Revision ID: 8f21407bb12a
Revises: 1af4eac8acb8
Create Date: 2026-10-27 03:22:32.211944
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f21407bb12a'
down_revision: Union[str, None] = '1af4eac8acb8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_messages",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
    )
    op.create_index(
        "ix_contact_messages_status_created",
        "contact_messages",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_messages_status_created", table_name="contact_messages")
    op.drop_column("contact_messages", "status")
