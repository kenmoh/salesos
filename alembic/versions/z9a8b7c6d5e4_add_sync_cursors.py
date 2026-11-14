"""add sync cursors table for offline sync

Revision ID: z9a8b7c6d5e4
Revises: b2c3d4e5f6a7
Create Date: 2026-11-14 19:12:05.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = 'z9a8b7c6d5e4'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sync_cursors',
        sa.Column('id', sa.UUID(), nullable=False, server_default=text("gen_random_uuid()")),
        sa.Column('tenant_id', sa.UUID(), nullable=False, index=True),
        sa.Column('device_id', sa.String(length=255), nullable=False),
        sa.Column('last_cursor', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=text("now()")),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'device_id', name='uq_sync_cursor_tenant_device'),
    )


def downgrade() -> None:
    op.drop_table('sync_cursors')
