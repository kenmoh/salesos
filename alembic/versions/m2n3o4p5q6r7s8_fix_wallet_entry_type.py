"""Allow fee_debit wallet entries.

debit_wallet() writes entry_type='fee_debit' but the original check
constraint omitted it, so fee settlement and plan upgrades failed.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "m2n3o4p5q6r7s8"
down_revision: Union[str, None] = "l2m3n4o5p6q7r8"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_ALLOWED = "'funding','invoice_debit','refund_credit','settlement','adjustment','fee_debit'"


def upgrade() -> None:
    op.execute("ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS ck_wallet_entry_type")
    op.execute(
        f"ALTER TABLE wallet_transactions ADD CONSTRAINT ck_wallet_entry_type "
        f"CHECK (entry_type IN ({_ALLOWED}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS ck_wallet_entry_type")
    op.execute(
        "ALTER TABLE wallet_transactions ADD CONSTRAINT ck_wallet_entry_type "
        "CHECK (entry_type IN ('funding','invoice_debit','refund_credit','settlement','adjustment'))"
    )
