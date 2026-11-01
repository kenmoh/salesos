"""Distribution workflow: warehouse dispatches to one store, store receives.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = [
    ("inventory:distribution_create", "Create warehouse distributions"),
    ("inventory:distribution_receive", "Receive warehouse distributions"),
    ("inventory:distribution_read", "View distributions"),
]


def upgrade() -> None:
    op.create_table(
        "distributions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("distribution_number", sa.String(length=20), nullable=False),
        sa.Column("from_store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("dispatched_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("pin_verified_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "distribution_number", name="uq_distribution_tenant_number"
        ),
    )
    op.create_index(
        op.f("ix_distributions_tenant_from_status"),
        "distributions",
        ["tenant_id", "from_store_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_distributions_tenant_to_status"),
        "distributions",
        ["tenant_id", "to_store_id", "status"],
        unique=False,
    )

    op.create_table(
        "distribution_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("distribution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("qty_dispatched", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("qty_received", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("qty_damaged", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("condition_dispatched", sa.String(length=20), nullable=True),
        sa.Column("condition_received", sa.String(length=20), nullable=True),
        sa.Column("damage_notes", sa.Text(), nullable=True),
        sa.Column("receive_notes", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["distribution_id"],
            ["distributions.id"],
            name="fk_distribution_items_distribution_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_distribution_items_tenant_id"),
        "distribution_items",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_distribution_items_distribution_id"),
        "distribution_items",
        ["distribution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_distribution_items_source_product_id"),
        "distribution_items",
        ["source_product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_distribution_items_destination_product_id"),
        "distribution_items",
        ["destination_product_id"],
        unique=False,
    )

    op.create_table(
        "stock_damages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("distribution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("distribution_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qty", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_stock_damages_tenant_id"), "stock_damages", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_stock_damages_distribution_id"),
        "stock_damages",
        ["distribution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stock_damages_distribution_item_id"),
        "stock_damages",
        ["distribution_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stock_damages_store_id"), "stock_damages", ["store_id"], unique=False
    )
    op.create_index(
        op.f("ix_stock_damages_product_id"), "stock_damages", ["product_id"], unique=False
    )

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
    bind = op.get_bind()
    for name, _ in PERMISSIONS:
        bind.execute(
            sa.text("DELETE FROM permissions WHERE name = :name"), {"name": name}
        )
    op.drop_index(op.f("ix_stock_damages_product_id"), table_name="stock_damages")
    op.drop_index(op.f("ix_stock_damages_store_id"), table_name="stock_damages")
    op.drop_index(
        op.f("ix_stock_damages_distribution_item_id"), table_name="stock_damages"
    )
    op.drop_index(op.f("ix_stock_damages_distribution_id"), table_name="stock_damages")
    op.drop_index(op.f("ix_stock_damages_tenant_id"), table_name="stock_damages")
    op.drop_table("stock_damages")
    op.drop_index(
        op.f("ix_distribution_items_destination_product_id"),
        table_name="distribution_items",
    )
    op.drop_index(
        op.f("ix_distribution_items_source_product_id"), table_name="distribution_items"
    )
    op.drop_index(
        op.f("ix_distribution_items_distribution_id"), table_name="distribution_items"
    )
    op.drop_index(op.f("ix_distribution_items_tenant_id"), table_name="distribution_items")
    op.drop_table("distribution_items")
    op.drop_index(op.f("ix_distributions_tenant_to_status"), table_name="distributions")
    op.drop_index(
        op.f("ix_distributions_tenant_from_status"), table_name="distributions"
    )
    op.drop_table("distributions")
