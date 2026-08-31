"""seed_permissions

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


PERMISSIONS = [
    ("auth:login", "Login to the system"),
    ("auth:manage_totp", "Manage TOTP two-factor authentication"),
    ("auth:manage_sessions", "View and revoke active sessions"),
    ("users:create", "Create new user accounts"),
    ("users:read", "View user accounts"),
    ("users:update", "Update user accounts"),
    ("roles:read", "View roles"),
    ("roles:assign", "Assign roles to users"),
    ("products:create", "Create products"),
    ("products:read", "View products"),
    ("products:update", "Update products"),
    ("products:delete", "Delete products"),
    ("categories:create", "Create product categories"),
    ("categories:read", "View product categories"),
    ("categories:update", "Update product categories"),
    ("categories:delete", "Delete product categories"),
    ("inventory:read", "View inventory"),
    ("inventory:write", "Create and update inventory records"),
    ("inventory:adjust", "Perform stock adjustments"),
    ("inventory:transfer", "Transfer stock between stores"),
    ("inventory:transfer_request", "Request inter-store transfers"),
    ("inventory:transfer_approve", "Approve inter-store transfer requests"),
    ("inventory:transfer_fulfill", "Fulfill approved inter-store transfers"),
    ("warehouses:create", "Create warehouses"),
    ("warehouses:read", "View warehouses"),
    ("warehouses:update", "Update warehouses"),
    ("sales:create", "Create sales"),
    ("sales:read", "View sales"),
    ("sales:void", "Void sales"),
    ("documents:create", "Create documents (invoices, receipts)"),
    ("documents:read", "View documents"),
    ("documents:update", "Update documents"),
    ("documents:manage", "Manage document templates"),
    ("payments:create", "Record payments"),
    ("payments:read", "View payments"),
    ("payments:confirm", "Confirm payments"),
    ("terminals:create", "Create terminals"),
    ("terminals:read", "View terminals"),
    ("terminals:update", "Update terminals"),
    ("employees:create", "Create employee accounts"),
    ("employees:read", "View employee accounts"),
    ("employees:update", "Update employee accounts"),
    ("employees:assign_roles", "Assign roles to employees"),
    ("accounting:read", "View accounting data"),
    ("accounting:write", "Create and update accounting entries"),
    ("accounting:post", "Post journal entries"),
    ("reports:read", "View reports"),
    ("admin:security", "Access admin security settings"),
    ("ai:generate", "Generate AI content"),
    ("ai:manage", "Manage AI settings"),
    ("ai:read", "View AI content"),
    ("cart:create", "Create shopping carts"),
    ("cart:read", "View shopping carts"),
    ("cart:delete", "Delete shopping carts"),
    ("cart:checkout", "Process cart checkout"),
    ("sync:read", "View sync data"),
    ("sync:manage", "Manage sync settings"),
    ("stores:create", "Create stores"),
    ("stores:read", "View stores"),
    ("stores:update", "Update stores"),
    ("stores:manage_main", "Manage main store settings"),
    ("customers:create", "Create customers"),
    ("customers:read", "View customers"),
    ("customers:update", "Update customers"),
    ("customers:delete", "Delete customers"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for name, description in PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, name, description) "
                "VALUES (gen_random_uuid(), :name, :description) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"name": name, "description": description},
        )


def downgrade() -> None:
    op.execute("DELETE FROM permissions")
