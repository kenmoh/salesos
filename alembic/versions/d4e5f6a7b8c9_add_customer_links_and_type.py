"""link documents to customers, add customer type, relax AR customer_id

Documents stored customer details as free text only, so nothing could tie an
invoice back to a customer record: the app had to ask a user to type a customer
id by hand when recording a receivable, and the receivable list could not be
joined to customers. Customers also had no way to record whether they are
someone we sell to or someone we buy from.

1. documents.customer_id (nullable) + tenant/customer index, backfilled by
   matching the free-text customer_name against customers.name.
2. accounts_receivable.customer_id becomes nullable. Invoice-created AR rows
   were storing the document creator's user id in that column, which is not a
   customer at all; the backfill below repairs those rows and the worker now
   writes NULL when the document has no customer.
3. customers.type ('customer' | 'vendor'), defaulting existing rows to
   'customer'.

Revision ID: d4e5f6a7b8c9
Revises: c8d9e0f1a2b3
"""

from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Documents point at a customer record ─────────────────────────────
    op.execute("ALTER TABLE documents ADD COLUMN customer_id UUID")
    op.execute(
        "CREATE INDEX idx_documents_tenant_customer "
        "ON documents(tenant_id, customer_id)"
    )

    # Backfill from the free-text name. Ambiguous names (two customers with
    # the same name in one tenant) resolve to a single arbitrary match; the
    # column is nullable and the app re-links on the next edit.
    op.execute("""
        UPDATE documents d
        SET customer_id = sub.customer_id
        FROM (
            SELECT DISTINCT ON (d2.id)
                   d2.id AS doc_id,
                   c.id AS customer_id
            FROM documents d2
            JOIN customers c
              ON c.tenant_id = d2.tenant_id
             AND lower(btrim(d2.customer_name)) = lower(btrim(c.name))
            WHERE d2.customer_id IS NULL
              AND d2.customer_name IS NOT NULL
            ORDER BY d2.id, c.created_at
        ) AS sub
        WHERE d.id = sub.doc_id
    """)

    # ── AR customer_id holds a real customer id, or nothing ─────────────
    op.execute(
        "ALTER TABLE accounts_receivable ALTER COLUMN customer_id DROP NOT NULL"
    )

    # The worker wrote documents.created_by (a user id) into AR.customer_id.
    # Replace it with the document's customer where we now know it.
    op.execute("""
        UPDATE accounts_receivable ar
        SET customer_id = d.customer_id
        FROM documents d
        WHERE ar.invoice_id = d.id
          AND d.customer_id IS NOT NULL
          AND ar.customer_id = d.created_by
    """)

    # ── Customers are either someone we sell to or someone we buy from ──
    op.execute(
        "ALTER TABLE customers ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'customer'"
    )
    op.execute("""
        ALTER TABLE customers ADD CONSTRAINT ck_customers_type
        CHECK (type IN ('customer', 'vendor'))
    """)
    op.execute(
        "CREATE INDEX idx_customers_tenant_type ON customers(tenant_id, type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_customers_tenant_type")
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customers_type")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS type")

    # AR rows written with a NULL customer_id cannot satisfy NOT NULL again.
    # Restore the old guarantee for the rows that still have a value and leave
    # the column nullable rather than deleting financial records.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM accounts_receivable WHERE customer_id IS NULL
            ) THEN
                ALTER TABLE accounts_receivable
                    ALTER COLUMN customer_id SET NOT NULL;
            END IF;
        END $$;
    """)

    op.execute("DROP INDEX IF EXISTS idx_documents_tenant_customer")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS customer_id")
