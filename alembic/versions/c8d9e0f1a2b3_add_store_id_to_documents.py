"""add store_id to documents and backfill invoice-created AR

Documents carry no store, so AR rows created when an invoice transitions to
"sent" could not be attributed to a store — per-store receivables would have
shown nothing under a selected store. The document create API now accepts an
optional store_id (the app sends the active store), the worker tags the AR it
creates from the document's store, and existing rows are backfilled where a
store can be derived:

1. documents.store_id (nullable) + tenant+store index.
2. Backfill documents from the sale they were linked to.
3. Backfill accounts_receivable.store_id from the linked document.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN store_id UUID")
    op.execute(
        "CREATE INDEX idx_documents_tenant_store ON documents(tenant_id, store_id)"
    )

    # ── Documents inherit the store of the sale they were linked to ──────
    op.execute("""
        UPDATE documents d
        SET store_id = s.store_id
        FROM sales s
        WHERE d.linked_sale_id = s.id
          AND d.store_id IS NULL
          AND s.store_id IS NOT NULL
    """)

    # ── AR created from invoices inherits the document's store ───────────
    op.execute("""
        UPDATE accounts_receivable ar
        SET store_id = d.store_id
        FROM documents d
        WHERE ar.invoice_id = d.id
          AND ar.store_id IS NULL
          AND d.store_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_tenant_store")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS store_id")
