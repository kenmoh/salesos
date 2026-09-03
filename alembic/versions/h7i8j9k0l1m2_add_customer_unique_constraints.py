"""Add unique constraints on customer email and phone per tenant.

Revision ID: h7i8j9k0l1m2
Revises: g6h7i8j9k0l1
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa

revision = "h7i8j9k0l1m2"
down_revision = "g6h7i8j9k0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_customer_tenant_email",
        "customers",
        ["tenant_id", "email"],
    )
    op.create_unique_constraint(
        "uq_customer_tenant_phone",
        "customers",
        ["tenant_id", "phone"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_customer_tenant_phone", "customers", type_="unique")
    op.drop_constraint("uq_customer_tenant_email", "customers", type_="unique")
