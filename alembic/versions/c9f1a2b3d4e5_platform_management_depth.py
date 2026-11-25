"""Platform management depth: notes, tags, status history, impersonation,
setting history and data requests.

Revision ID: c9f1a2b3d4e5
Revises: f8a9b0c1d2e3
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c9f1a2b3d4e5"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "platform_tenant_notes",
        _uuid_pk(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("admin_id", UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False, server_default="general"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamps(),
    )
    op.create_index("ix_platform_tenant_notes_tenant_id", "platform_tenant_notes", ["tenant_id"])
    op.create_index("ix_platform_tenant_notes_admin_id", "platform_tenant_notes", ["admin_id"])

    op.create_table(
        "platform_tenant_tags",
        _uuid_pk(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(50), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("tenant_id", "tag", name="uq_platform_tenant_tag"),
    )
    op.create_index("ix_platform_tenant_tags_tenant_id", "platform_tenant_tags", ["tenant_id"])

    op.create_table(
        "platform_tenant_status_history",
        _uuid_pk(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("admin_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_platform_tenant_status_history_tenant_id",
        "platform_tenant_status_history",
        ["tenant_id"],
    )

    op.create_table(
        "platform_impersonation_log",
        _uuid_pk(),
        sa.Column("admin_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_platform_impersonation_log_admin_id", "platform_impersonation_log", ["admin_id"])
    op.create_index("ix_platform_impersonation_log_tenant_id", "platform_impersonation_log", ["tenant_id"])

    op.create_table(
        "platform_setting_history",
        _uuid_pk(),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("admin_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_platform_setting_history_key", "platform_setting_history", ["key"])

    op.create_table(
        "platform_data_requests",
        _uuid_pk(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("request_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_platform_data_requests_tenant_id", "platform_data_requests", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("platform_data_requests")
    op.drop_table("platform_setting_history")
    op.drop_table("platform_impersonation_log")
    op.drop_table("platform_tenant_status_history")
    op.drop_table("platform_tenant_tags")
    op.drop_table("platform_tenant_notes")
