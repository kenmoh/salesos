"""add tax types and store tax_enabled

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create taxes table
    op.create_table(
        "taxes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("rate", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Add tax_enabled to stores
    op.add_column(
        "stores",
        sa.Column("tax_enabled", sa.Boolean, nullable=False, server_default="false"),
    )

    # Replace tax_rate with tax_id FK on products
    op.drop_column("products", "tax_rate")
    op.add_column(
        "products",
        sa.Column("tax_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key("fk_products_tax_id", "products", "taxes", ["tax_id"], ["id"])

    # Replace tax_rate with tax_id FK on store_products
    op.drop_column("store_products", "tax_rate")
    op.add_column(
        "store_products",
        sa.Column("tax_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key("fk_store_products_tax_id", "store_products", "taxes", ["tax_id"], ["id"])

    # Add tax_id FK to sale_items (keep tax_rate as denormalized snapshot)
    op.add_column(
        "sale_items",
        sa.Column("tax_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sale_items", "tax_id")

    op.drop_constraint("fk_store_products_tax_id", "store_products", type_="foreignkey")
    op.drop_column("store_products", "tax_id")
    op.add_column(
        "store_products",
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=True),
    )

    op.drop_constraint("fk_products_tax_id", "products", type_="foreignkey")
    op.drop_column("products", "tax_id")
    op.add_column(
        "products",
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=True),
    )

    op.drop_column("stores", "tax_enabled")
    op.drop_table("taxes")
