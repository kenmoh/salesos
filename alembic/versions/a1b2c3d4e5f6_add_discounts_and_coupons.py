"""Add discounts, discount_products, discount_categories, coupons tables.

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── discounts ────────────────────────────────────────────────────────
    op.create_table(
        "discounts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("discount_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("buy_x_get_y_free_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scope", sa.String(20), nullable=False, server_default="all"),
        sa.Column("min_order", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── discount_products ────────────────────────────────────────────────
    op.create_table(
        "discount_products",
        sa.Column(
            "discount_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.UniqueConstraint("discount_id", "product_id", name="uq_discount_product"),
    )

    # ── discount_categories ──────────────────────────────────────────────
    op.create_table(
        "discount_categories",
        sa.Column(
            "discount_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("category_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.UniqueConstraint("discount_id", "category_id", name="uq_discount_category"),
    )

    # ── coupons ──────────────────────────────────────────────────────────
    op.create_table(
        "coupons",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("discount_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("max_uses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("used_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("min_order", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("coupons")
    op.drop_table("discount_categories")
    op.drop_table("discount_products")
    op.drop_table("discounts")
