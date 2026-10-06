"""add AR/AP payment ledgers and stop overpayment

Recording a payment only moved money on the parent row: amount_paid went up,
balance came down, and the payment date and notes the API accepted were
discarded. There was no record of *which* payments settled an invoice, so a
part payment history could not be shown or audited, and nothing stopped two
devices from paying the same balance twice and driving it negative.

1. ar_payments / ap_payments ledger tables, one row per payment.
2. fn_record_ar_payment / fn_record_ap_payment write the ledger row, lock the
   parent row, reject amounts above the remaining balance, and return the new
   payment id (or an out_error message the API turns into a 400).
3. recorded_by captures who took the payment.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Payment ledgers ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ar_payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            ar_id UUID NOT NULL,
            amount NUMERIC(15, 2) NOT NULL CHECK (amount > 0),
            payment_date DATE NOT NULL,
            notes TEXT,
            recorded_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ar_payments_tenant_ar "
        "ON ar_payments(tenant_id, ar_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ar_payments_tenant_date "
        "ON ar_payments(tenant_id, payment_date DESC)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS ap_payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            ap_id UUID NOT NULL,
            amount NUMERIC(15, 2) NOT NULL CHECK (amount > 0),
            payment_date DATE NOT NULL,
            notes TEXT,
            recorded_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ap_payments_tenant_ap "
        "ON ap_payments(tenant_id, ap_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ap_payments_tenant_date "
        "ON ap_payments(tenant_id, payment_date DESC)"
    )

    # Same tenant isolation as every other tenant-scoped table.
    for table in ("ar_payments", "ap_payments"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id::text = current_setting('app.business_id', true))
                WITH CHECK (tenant_id::text = current_setting('app.business_id', true))
        """)

    # ── Recording a payment writes the ledger and refuses overpayment ────
    op.execute("DROP FUNCTION IF EXISTS fn_record_ar_payment(uuid,uuid,numeric,text,text)")
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_record_ar_payment(
            p_tenant_id UUID, p_ar_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL,
            p_recorded_by UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, out_payment_id UUID, out_error TEXT
        ) AS $$
        DECLARE
            v_row accounts_receivable%ROWTYPE;
            v_new_paid NUMERIC;
            v_new_balance NUMERIC;
            v_new_status VARCHAR;
            v_payment_id UUID := gen_random_uuid();
            v_date DATE := COALESCE(NULLIF(p_payment_date, '')::DATE, CURRENT_DATE);
            v_error TEXT := NULL;
        BEGIN
            -- Lock the row so two concurrent payments cannot both pass the
            -- balance check below.
            SELECT * INTO v_row
            FROM accounts_receivable
            WHERE id = p_ar_id AND tenant_id = p_tenant_id
            FOR UPDATE;

            IF NOT FOUND THEN
                v_error := 'Receivable not found';
            ELSIF p_amount IS NULL OR p_amount <= 0 THEN
                v_error := 'Payment amount must be greater than 0';
            ELSIF p_amount > v_row.balance THEN
                v_error := 'Payment amount exceeds the remaining balance of '
                           || v_row.balance::TEXT;
            END IF;

            IF v_error IS NOT NULL THEN
                RETURN QUERY SELECT
                    p_ar_id, p_tenant_id, NULL::UUID, NULL::VARCHAR, NULL::VARCHAR,
                    NULL::NUMERIC, NULL::NUMERIC, NULL::NUMERIC, NULL::TIMESTAMPTZ,
                    NULL::VARCHAR, NULL::UUID, v_error;
                RETURN;
            END IF;

            v_new_paid := v_row.amount_paid + p_amount;
            v_new_balance := v_row.balance - p_amount;
            v_new_status := CASE WHEN v_new_balance <= 0 THEN 'paid' ELSE 'partial' END;

            UPDATE accounts_receivable
            SET amount_paid = v_new_paid,
                balance = v_new_balance,
                status = v_new_status
            WHERE id = p_ar_id AND tenant_id = p_tenant_id;

            INSERT INTO ar_payments (
                id, tenant_id, ar_id, amount, payment_date, notes, recorded_by
            ) VALUES (
                v_payment_id, p_tenant_id, p_ar_id, p_amount, v_date, p_notes, p_recorded_by
            );

            RETURN QUERY SELECT
                v_row.id, v_row.tenant_id, v_row.customer_id, v_row.customer_name,
                v_row.invoice_number, v_row.amount, v_new_paid, v_new_balance,
                v_row.due_date, v_new_status, v_payment_id, NULL::TEXT;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("DROP FUNCTION IF EXISTS fn_record_ap_payment(uuid,uuid,numeric,text,text)")
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_record_ap_payment(
            p_tenant_id UUID, p_ap_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL,
            p_recorded_by UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC, balance NUMERIC,
            due_date TIMESTAMPTZ, status VARCHAR, out_payment_id UUID, out_error TEXT
        ) AS $$
        DECLARE
            v_row accounts_payable%ROWTYPE;
            v_new_paid NUMERIC;
            v_new_balance NUMERIC;
            v_new_status VARCHAR;
            v_payment_id UUID := gen_random_uuid();
            v_date DATE := COALESCE(NULLIF(p_payment_date, '')::DATE, CURRENT_DATE);
            v_error TEXT := NULL;
        BEGIN
            SELECT * INTO v_row
            FROM accounts_payable
            WHERE id = p_ap_id AND tenant_id = p_tenant_id
            FOR UPDATE;

            IF NOT FOUND THEN
                v_error := 'Payable not found';
            ELSIF p_amount IS NULL OR p_amount <= 0 THEN
                v_error := 'Payment amount must be greater than 0';
            ELSIF p_amount > v_row.balance THEN
                v_error := 'Payment amount exceeds the remaining balance of '
                           || v_row.balance::TEXT;
            END IF;

            IF v_error IS NOT NULL THEN
                RETURN QUERY SELECT
                    p_ap_id, p_tenant_id, NULL::VARCHAR, NULL::VARCHAR, NULL::TEXT,
                    NULL::NUMERIC, NULL::NUMERIC, NULL::NUMERIC, NULL::TIMESTAMPTZ,
                    NULL::VARCHAR, NULL::UUID, v_error;
                RETURN;
            END IF;

            v_new_paid := v_row.amount_paid + p_amount;
            v_new_balance := v_row.balance - p_amount;
            v_new_status := CASE WHEN v_new_balance <= 0 THEN 'paid' ELSE 'partial' END;

            UPDATE accounts_payable
            SET amount_paid = v_new_paid,
                balance = v_new_balance,
                status = v_new_status
            WHERE id = p_ap_id AND tenant_id = p_tenant_id;

            INSERT INTO ap_payments (
                id, tenant_id, ap_id, amount, payment_date, notes, recorded_by
            ) VALUES (
                v_payment_id, p_tenant_id, p_ap_id, p_amount, v_date, p_notes, p_recorded_by
            );

            RETURN QUERY SELECT
                v_row.id, v_row.tenant_id, v_row.bill_number, v_row.vendor_name,
                v_row.description, v_row.amount, v_new_paid, v_new_balance,
                v_row.due_date, v_new_status, v_payment_id, NULL::TEXT;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_record_ar_payment(uuid,uuid,numeric,text,text,uuid)")
    op.execute("DROP FUNCTION IF EXISTS fn_record_ap_payment(uuid,uuid,numeric,text,text,uuid)")
    op.execute("DROP TABLE IF EXISTS ap_payments")
    op.execute("DROP TABLE IF EXISTS ar_payments")
