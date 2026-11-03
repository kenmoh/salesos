"""flat per-document fee

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-11-02

Documents are now charged a flat rate at creation instead of a share of their
total. The rate lives on platform_commissions.document_fee (platform-admin
configurable, 0 = no charge until set). Each document's fee is one ledger row
marked fee_category='document'; sale commission rows stay 'commission'.
"""
from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a9"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_commissions",
        sa.Column("document_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "platform_fee_ledger",
        sa.Column("fee_category", sa.String(20), nullable=False, server_default="commission"),
    )
    # Historical standalone receipts were the only rows charged against a
    # document; converted documents were charged against their sale.
    op.execute(
        "UPDATE platform_fee_ledger SET fee_category = 'document' "
        "WHERE document_id IS NOT NULL AND sale_id IS NULL"
    )
    op.create_index(
        "ix_platform_fee_ledger_fee_category", "platform_fee_ledger", ["fee_category"]
    )


def downgrade() -> None:
    op.drop_index("ix_platform_fee_ledger_fee_category", table_name="platform_fee_ledger")
    op.drop_column("platform_fee_ledger", "fee_category")
    op.drop_column("platform_commissions", "document_fee")
