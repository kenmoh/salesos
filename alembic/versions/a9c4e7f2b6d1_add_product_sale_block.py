"""Products can be blocked from sale after they expire.

The flag is off by default so existing products keep selling exactly as they
do today. Only rows that opt in become unsellable on the POS once their
expiry day has passed.

Revision ID: a9c4e7f2b6d1
Revises: f4a7c9e2b1d8
"""

from alembic import op

revision = "a9c4e7f2b6d1"
down_revision = "f4a7c9e2b1d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE products ADD COLUMN block_sale_after_expiry "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE products DROP COLUMN block_sale_after_expiry")
