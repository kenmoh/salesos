"""Heal sale-based receivables: create missing AR subledger rows.

When a sale is booked unpaid (``settled=False``), the GL debits account
1100 but no ``accounts_receivable`` row was created, so the customer list
showed nothing while the control account showed the full receivable. This
backfill creates the missing AR row for every sale that has a net 1100
debit from a ``sale`` journal and no existing AR row. Payments recorded
against those sales after this migration post the cash leg and draw the
row down through ``record_payment``.

Revision ID: z7a8b9c0d1e2
Revises: b1c2d3e4f5a6
"""

from alembic import op
import sqlalchemy as sa

revision = "z7a8b9c0d1e2"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO accounts_receivable (
            id, tenant_id, invoice_id, customer_id, customer_name,
            invoice_number, amount, amount_paid, balance, due_date,
            status, store_id, created_at
        )
        SELECT
            gen_random_uuid(),
            t.tenant_id,
            t.reference_id,
            NULL,
            COALESCE(s.customer_name, 'Walk-in'),
            s.sale_number,
            s.total,
            s.amount_paid,
            GREATEST(s.total - s.amount_paid, 0),
            NOW(),
            CASE
                WHEN s.amount_paid >= s.total THEN 'paid'
                WHEN s.amount_paid > 0 THEN 'partial'
                ELSE 'pending'
            END,
            s.store_id,
            NOW()
        FROM (
            SELECT
                j.tenant_id,
                j.reference_id,
                SUM(e.debit) - SUM(e.credit) AS net_1100
            FROM journal_entries e
            JOIN journals j ON j.id = e.journal_id
            WHERE e.account_code = '1100'
              AND j.reference_type = 'sale'
            GROUP BY j.tenant_id, j.reference_id
            HAVING SUM(e.debit) - SUM(e.credit) > 0.009
        ) t
        JOIN sales s ON s.id = t.reference_id
        WHERE NOT EXISTS (
            SELECT 1 FROM accounts_receivable ar
            WHERE ar.invoice_id = t.reference_id
        )
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM accounts_receivable ar
        USING sales s
        WHERE ar.invoice_id = s.id
          AND ar.customer_name = COALESCE(s.customer_name, 'Walk-in')
    """)
