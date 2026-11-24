"""Allow fee settlement entry types in billing fee ledger

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENTRY_TYPES = (
    "'charge', 'credit', 'reversal', 'adjustment', "
    "'payment', "
    "'sale_charge', 'sale_payment', 'sale_accrual', "
    "'document_charge', 'document_payment', 'document_accrual'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_billing_entry_type",
        "billing_fee_ledger",
        type_="check",
    )
    op.create_check_constraint(
        "ck_billing_entry_type",
        "billing_fee_ledger",
        f"entry_type IN ({_ENTRY_TYPES})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_billing_entry_type",
        "billing_fee_ledger",
        type_="check",
    )
    op.create_check_constraint(
        "ck_billing_entry_type",
        "billing_fee_ledger",
        "entry_type IN ('charge', 'credit', 'reversal', 'adjustment')",
    )
