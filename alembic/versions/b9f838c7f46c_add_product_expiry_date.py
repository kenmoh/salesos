"""Products gain an optional expiry date.

Shelf-to-counter shop owners need to see when a product goes off. The date
is optional (many goods never expire), and the mobile inventory screens flag
rows whose date has passed. Stored date-only: a product expires at the end
of its printed day, not at a clock time.

Revision ID: b9f838c7f46c
Revises: b7c8d9e0f1a3
"""

from alembic import op

revision = "b9f838c7f46c"
down_revision = "b7c8d9e0f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE products ADD COLUMN expiry_date DATE")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_expiry_date "
        "ON products (expiry_date) WHERE expiry_date IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_expiry_date")
    op.execute("ALTER TABLE products DROP COLUMN expiry_date")
