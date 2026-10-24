"""Products can warn before their expiry date.

A warning window belongs to the product because milk and canned goods do not
share a shelf life. It is optional: NULL means no near-expiry warning, while
past expiry is always flagged by the inventory card.

Revision ID: f4a7c9e2b1d8
Revises: b9f838c7f46c
"""

from alembic import op

revision = "f4a7c9e2b1d8"
down_revision = "b9f838c7f46c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE products ADD COLUMN expiry_warning_days INTEGER")
    op.execute(
        "ALTER TABLE products ADD CONSTRAINT ck_products_expiry_warning_days "
        "CHECK (expiry_warning_days IS NULL "
        "OR expiry_warning_days BETWEEN 0 AND 3650)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE products DROP CONSTRAINT IF EXISTS ck_products_expiry_warning_days"
    )
    op.execute("ALTER TABLE products DROP COLUMN expiry_warning_days")
