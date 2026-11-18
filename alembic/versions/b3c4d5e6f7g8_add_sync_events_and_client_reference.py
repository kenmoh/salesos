"""Add sync_events table and sales.client_reference for offline sync.

Revision ID: b3c4d5e6f7g8
Revises: t1i2p3s4x5y6
Create Date: 2026-11-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b3c4d5e6f7g8'
down_revision: Union[str, None] = 't1i2p3s4x5y6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sync_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('client_id', sa.String(length=255), nullable=False, index=True),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('client_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending', index=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_sync_events_tenant_status', 'sync_events', ['tenant_id', 'status'],
    )
    op.create_index(
        'ix_sync_events_tenant_client', 'sync_events', ['tenant_id', 'client_id'], unique=True,
    )

    op.add_column(
        'sales',
        sa.Column('client_reference', sa.String(length=255), nullable=True, index=True),
    )
    op.create_index(
        'uq_sales_tenant_client_reference',
        'sales',
        ['tenant_id', 'client_reference'],
        unique=True,
        postgresql_where=sa.text("client_reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index('uq_sales_tenant_client_reference', table_name='sales', postgresql_where=sa.text("client_reference IS NOT NULL"))
    op.drop_column('sales', 'client_reference')
    op.drop_index('ix_sync_events_tenant_client', table_name='sync_events')
    op.drop_index('ix_sync_events_tenant_status', table_name='sync_events')
    op.drop_table('sync_events')
