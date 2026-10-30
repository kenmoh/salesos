"""Extend transfer_requests into a full request/approve/reject/fulfil workflow.

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transfer_requests", sa.Column("request_number", sa.String(length=30), nullable=True)
    )
    op.add_column("transfer_requests", sa.Column("item_name", sa.String(length=200), nullable=True))
    op.add_column(
        "transfer_requests", sa.Column("supplying_product_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "transfer_requests", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "transfer_requests", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("transfer_requests", sa.Column("fulfilled_by", sa.UUID(), nullable=True))
    op.add_column(
        "transfer_requests", sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("transfer_requests", sa.Column("fulfilled_qty", sa.Numeric(12, 2), nullable=True))
    op.add_column("transfer_requests", sa.Column("pin_verified_by", sa.UUID(), nullable=True))
    op.add_column("transfer_requests", sa.Column("decision_note", sa.Text(), nullable=True))

    # Legacy rows keep working: they get a reference number, and the single
    # product id they were created with stands in for both sides of the move.
    op.execute(
        """
        UPDATE transfer_requests
        SET request_number = 'TR-' || UPPER(SUBSTRING(REPLACE(id::text, '-', '') FROM 1 FOR 8)),
            supplying_product_id = product_id
        WHERE request_number IS NULL
        """
    )
    op.execute(
        """
        UPDATE transfer_requests t
        SET item_name = COALESCE(
            (
                SELECT sp.name
                FROM store_products sp
                WHERE sp.product_id = t.product_id
                  AND sp.store_id = t.requesting_store_id
                LIMIT 1
            ),
            (SELECT p.name FROM products p WHERE p.id = t.product_id)
        )
        WHERE t.item_name IS NULL
        """
    )

    op.create_index(
        "uq_transfer_requests_tenant_request_number",
        "transfer_requests",
        ["tenant_id", "request_number"],
        unique=True,
    )
    op.create_index(
        "ix_transfer_requests_supplying_status",
        "transfer_requests",
        ["tenant_id", "supplying_store_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_transfer_requests_requesting_status",
        "transfer_requests",
        ["tenant_id", "requesting_store_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transfer_requests_requesting_status", table_name="transfer_requests")
    op.drop_index("ix_transfer_requests_supplying_status", table_name="transfer_requests")
    op.drop_index("uq_transfer_requests_tenant_request_number", table_name="transfer_requests")
    op.drop_column("transfer_requests", "decision_note")
    op.drop_column("transfer_requests", "pin_verified_by")
    op.drop_column("transfer_requests", "fulfilled_qty")
    op.drop_column("transfer_requests", "fulfilled_at")
    op.drop_column("transfer_requests", "fulfilled_by")
    op.drop_column("transfer_requests", "rejected_at")
    op.drop_column("transfer_requests", "approved_at")
    op.drop_column("transfer_requests", "supplying_product_id")
    op.drop_column("transfer_requests", "item_name")
    op.drop_column("transfer_requests", "request_number")
