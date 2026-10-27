"""Composite indexes backing the per-tenant audit trail.

- audit_events(business_id, created_at): business-action layer looks up by
  tenant and sorts by time.
- auth_audit_logs(tenant_id, created_at): auth layer filtered by tenant.
- platform_audit_log(resource, resource_id, created_at): platform actions
  recorded against a tenant.

Revision ID: 9c5d6e7f8a10
Revises: 8f21407bb12a
Create Date: 2026-10-27
"""

from alembic import op

revision = "9c5d6e7f8a10"
down_revision = "8f21407bb12a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_business_created",
        "audit_events",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_auth_audit_logs_tenant_created",
        "auth_audit_logs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_platform_audit_log_resource",
        "platform_audit_log",
        ["resource", "resource_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_audit_log_resource", table_name="platform_audit_log")
    op.drop_index("ix_auth_audit_logs_tenant_created", table_name="auth_audit_logs")
    op.drop_index("ix_audit_events_business_created", table_name="audit_events")
