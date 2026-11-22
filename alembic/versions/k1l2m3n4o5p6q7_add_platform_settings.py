"""Add platform_settings table for platform-wide configuration.

Stores key-value settings that only super_admins can manage, such as the
default debt cap for new tenants and the annual subscription discount.

Revision ID: k1l2m3n4o5p6q7
Revises: j0k1l2m3n4o5p6
Create Date: 2026-11-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "k1l2m3n4o5p6q7"
down_revision: Union[str, None] = "j0k1l2m3n4o5p6"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"), onupdate=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
