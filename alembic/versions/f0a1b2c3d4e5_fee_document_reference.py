"""let a platform fee reference a document instead of a sale

The platform fee is charged when a sale completes, so a document that never
becomes a sale escaped it entirely: a converted document left its sale pending
forever, and a standalone receipt has no sale at all. Charging for those two paths
needs a fee row that can point at a document.

``sale_id`` becomes nullable and ``document_id`` is added alongside it, with a
check that exactly one of the two is present. Existing rows are unaffected.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""

from alembic import op
import sqlalchemy as sa

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "platform_fee_ledger",
        "sale_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "platform_fee_ledger",
        sa.Column("document_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_platform_fee_ledger_document_id",
        "platform_fee_ledger",
        ["document_id"],
    )
    # A fee has to belong to something. Allowing neither would let an orphan row
    # in, and allowing both would double-count it against a sale and a document.
    op.create_check_constraint(
        "ck_platform_fee_ledger_one_source",
        "platform_fee_ledger",
        "num_nonnulls(sale_id, document_id) = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_platform_fee_ledger_one_source", "platform_fee_ledger")
    op.drop_index("ix_platform_fee_ledger_document_id", "platform_fee_ledger")
    op.drop_column("platform_fee_ledger", "document_id")
    op.alter_column(
        "platform_fee_ledger",
        "sale_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )