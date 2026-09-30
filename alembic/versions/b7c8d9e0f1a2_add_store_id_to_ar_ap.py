"""add store_id to accounts receivable/payable and per-store AR/AP filters

AR/AP were business-wide: neither table carried a store, so the dashboard's
outstanding receivable/payable figures and the AR/AP lists could not be
scoped to the active store. Invoices carry no store either, so AR rows can
only inherit a store when the invoice was converted to a sale (backfill via
documents.linked_sale_id); everything else stays NULL = business-wide.

Changes:
1. accounts_receivable.store_id / accounts_payable.store_id (nullable) +
   tenant+store indexes.
2. Backfill AR where the invoice was converted to a sale.
3. Recreate AR/AP fns (signature or return-type change, so old overloads
   are dropped first):
   - fn_list_accounts_receivable / fn_list_accounts_payable gain a
     p_store_id filter (NULL = all stores) and return store_id;
   - fn_create_accounts_receivable / fn_create_accounts_payable accept
     p_store_id and return store_id;
   - fn_record_ar_payment / fn_record_ap_payment also return store_id.
4. fn_financial_dashboard: outstanding AR/AP now respect p_store_id.

Postgres cannot change a function's signature or return type with
CREATE OR REPLACE, so each touched overload is dropped first. asyncpg
rejects multiple commands per prepared statement, hence one op.execute
per statement.

Revision ID: b7c8d9e0f1a2
Revises: y4z5a6b7c8d9
"""

from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "y4z5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Columns + indexes ─────────────────────────────────────────────
    op.execute("ALTER TABLE accounts_receivable ADD COLUMN store_id UUID")
    op.execute("ALTER TABLE accounts_payable ADD COLUMN store_id UUID")
    op.execute(
        "CREATE INDEX idx_accounts_receivable_tenant_store "
        "ON accounts_receivable(tenant_id, store_id)"
    )
    op.execute(
        "CREATE INDEX idx_accounts_payable_tenant_store "
        "ON accounts_payable(tenant_id, store_id)"
    )

    # ── 2. Backfill AR where the invoice was converted to a sale ─────────
    op.execute("""
        UPDATE accounts_receivable ar
        SET store_id = s.store_id
        FROM documents d
        JOIN sales s ON s.id = d.linked_sale_id
        WHERE ar.invoice_id = d.id
          AND ar.store_id IS NULL
          AND s.store_id IS NOT NULL
    """)

    # ── 3. Drop old overloads (signature/return-type change) ─────────────
    for sig in [
        "fn_list_accounts_receivable(uuid, varchar)",
        "fn_create_accounts_receivable(uuid, uuid, varchar, varchar, numeric, text, uuid)",
        "fn_create_accounts_receivable(uuid, uuid, varchar, varchar, numeric, uuid)",
        "fn_record_ar_payment(uuid, uuid, numeric, date, text)",
        "fn_record_ar_payment(uuid, uuid, numeric, text, text)",
        "fn_list_accounts_payable(uuid, varchar)",
        "fn_create_accounts_payable(uuid, varchar, varchar, numeric, text, text)",
        "fn_create_accounts_payable(uuid, varchar, varchar, numeric, date, text)",
        "fn_record_ap_payment(uuid, uuid, numeric, date, text)",
        "fn_record_ap_payment(uuid, uuid, numeric, text, text)",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {sig} CASCADE")

    # ── 4. Recreate AR/AP fns with store awareness ───────────────────────
    op.execute("""
        CREATE FUNCTION fn_list_accounts_receivable(
            p_tenant_id UUID, p_status VARCHAR DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status, ar.store_id
            FROM accounts_receivable ar
            WHERE ar.tenant_id = p_tenant_id
              AND (p_status IS NULL OR ar.status = p_status)
              AND (p_store_id IS NULL OR ar.store_id = p_store_id)
            ORDER BY ar.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_create_accounts_receivable(
            p_tenant_id UUID, p_customer_id UUID, p_customer_name VARCHAR,
            p_invoice_number VARCHAR, p_amount NUMERIC, p_due_date TEXT,
            p_invoice_id UUID DEFAULT NULL, p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, out_customer_id UUID,
            out_customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_receivable (
                id, tenant_id, invoice_id, customer_id, customer_name,
                invoice_number, amount, amount_paid, balance, due_date,
                status, created_at, store_id
            )
            VALUES (
                new_id, p_tenant_id, p_invoice_id, p_customer_id,
                p_customer_name, p_invoice_number, p_amount, 0, p_amount,
                p_due_date::DATE, 'pending', NOW(), p_store_id
            );
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status, ar.store_id
            FROM accounts_receivable ar WHERE ar.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_record_ar_payment(
            p_tenant_id UUID, p_ar_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        BEGIN
            UPDATE accounts_receivable ar2
            SET amount_paid = ar2.amount_paid + p_amount,
                balance = ar2.balance - p_amount,
                status = CASE WHEN ar2.balance - p_amount <= 0 THEN 'paid'
                              ELSE 'partial' END
            WHERE ar2.id = p_ar_id AND ar2.tenant_id = p_tenant_id;
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status, ar.store_id
            FROM accounts_receivable ar WHERE ar.id = p_ar_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_list_accounts_payable(
            p_tenant_id UUID, p_status VARCHAR DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC,
            balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR,
            store_id UUID
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status, ap.store_id
            FROM accounts_payable ap
            WHERE ap.tenant_id = p_tenant_id
              AND (p_status IS NULL OR ap.status = p_status)
              AND (p_store_id IS NULL OR ap.store_id = p_store_id)
            ORDER BY ap.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_create_accounts_payable(
            p_tenant_id UUID, p_bill_number VARCHAR, p_vendor_name VARCHAR,
            p_amount NUMERIC, p_due_date TEXT, p_description TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR,
            vendor_name VARCHAR, description TEXT, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_payable (
                id, tenant_id, bill_number, vendor_name, description,
                amount, amount_paid, balance, due_date, status, created_at,
                store_id
            )
            VALUES (
                new_id, p_tenant_id, p_bill_number, p_vendor_name,
                p_description, p_amount, 0, p_amount, p_due_date::DATE,
                'pending', NOW(), p_store_id
            );
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status, ap.store_id
            FROM accounts_payable ap WHERE ap.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_record_ap_payment(
            p_tenant_id UUID, p_ap_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR,
            vendor_name VARCHAR, description TEXT, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        BEGIN
            UPDATE accounts_payable ap2
            SET amount_paid = ap2.amount_paid + p_amount,
                balance = ap2.balance - p_amount,
                status = CASE WHEN ap2.balance - p_amount <= 0 THEN 'paid'
                              ELSE 'partial' END
            WHERE ap2.id = p_ap_id AND ap2.tenant_id = p_tenant_id;
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status, ap.store_id
            FROM accounts_payable ap WHERE ap.id = p_ap_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 5. fn_financial_dashboard: outstanding AR/AP respect p_store_id ──
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_financial_dashboard(
            p_tenant_id UUID, p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            cash_balance NUMERIC, outstanding_receivable NUMERIC,
            outstanding_payable NUMERIC, total_expenses_this_month NUMERIC,
            expense_by_category JSONB
        ) AS $$
        DECLARE
            cash_acct UUID;
            month_start TIMESTAMPTZ := DATE_TRUNC('month', NOW());
            result JSONB := '{}'::JSONB;
        BEGIN
            SELECT id INTO cash_acct FROM chart_of_accounts
            WHERE tenant_id = p_tenant_id AND code = '1000' LIMIT 1;

            SELECT COALESCE(
                (SELECT jsonb_object_agg(sub.category, sub.total)
                 FROM (
                    SELECT e.category, SUM(e.amount) AS total
                    FROM expenses e
                    WHERE e.tenant_id = p_tenant_id
                      AND e.expense_date >= month_start
                      AND (p_store_id IS NULL OR e.store_id = p_store_id)
                    GROUP BY e.category
                 ) sub),
                '{}'::JSONB
            ) INTO result;

            RETURN QUERY
            SELECT
                COALESCE((SELECT SUM(je.debit) - SUM(je.credit)
                    FROM journal_entries je
                    JOIN journals j ON j.id = je.journal_id
                    WHERE je.account_id = cash_acct AND je.status = 'posted'
                      AND (p_store_id IS NULL OR j.store_id = p_store_id)
                ), 0) AS cash_balance,
                COALESCE((SELECT SUM(ar.balance) FROM accounts_receivable ar
                    WHERE ar.tenant_id = p_tenant_id
                      AND ar.status IN ('pending','overdue','partial')
                      AND (p_store_id IS NULL OR ar.store_id = p_store_id)
                ), 0) AS outstanding_receivable,
                COALESCE((SELECT SUM(ap.balance) FROM accounts_payable ap
                    WHERE ap.tenant_id = p_tenant_id
                      AND ap.status IN ('pending','overdue','partial')
                      AND (p_store_id IS NULL OR ap.store_id = p_store_id)
                ), 0) AS outstanding_payable,
                COALESCE((SELECT SUM(e.amount) FROM expenses e
                    WHERE e.tenant_id = p_tenant_id
                      AND e.expense_date >= month_start
                      AND (p_store_id IS NULL OR e.store_id = p_store_id)
                ), 0) AS total_expenses_this_month,
                result AS expense_by_category;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # ── 1. Restore the pre-store AR/AP fn bodies ─────────────────────────
    for sig in [
        "fn_list_accounts_receivable(uuid, varchar, uuid)",
        "fn_create_accounts_receivable(uuid, uuid, varchar, varchar, numeric, text, uuid, uuid)",
        "fn_record_ar_payment(uuid, uuid, numeric, text, text)",
        "fn_list_accounts_payable(uuid, varchar, uuid)",
        "fn_create_accounts_payable(uuid, varchar, varchar, numeric, text, text, uuid)",
        "fn_record_ap_payment(uuid, uuid, numeric, text, text)",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {sig} CASCADE")

    op.execute("""
        CREATE FUNCTION fn_list_accounts_receivable(
            p_tenant_id UUID, p_status VARCHAR DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status
            FROM accounts_receivable ar
            WHERE ar.tenant_id = p_tenant_id
              AND (p_status IS NULL OR ar.status = p_status)
            ORDER BY ar.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_create_accounts_receivable(
            p_tenant_id UUID, p_customer_id UUID, p_customer_name VARCHAR,
            p_invoice_number VARCHAR, p_amount NUMERIC, p_due_date TEXT,
            p_invoice_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, out_customer_id UUID,
            out_customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_receivable (
                id, tenant_id, invoice_id, customer_id, customer_name,
                invoice_number, amount, amount_paid, balance, due_date,
                status, created_at
            )
            VALUES (
                new_id, p_tenant_id, p_invoice_id, p_customer_id,
                p_customer_name, p_invoice_number, p_amount, 0, p_amount,
                p_due_date::DATE, 'pending', NOW()
            );
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status
            FROM accounts_receivable ar WHERE ar.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_record_ar_payment(
            p_tenant_id UUID, p_ar_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR
        ) AS $$
        BEGIN
            UPDATE accounts_receivable ar2
            SET amount_paid = ar2.amount_paid + p_amount,
                balance = ar2.balance - p_amount,
                status = CASE WHEN ar2.balance - p_amount <= 0 THEN 'paid'
                              ELSE 'partial' END
            WHERE ar2.id = p_ar_id AND ar2.tenant_id = p_tenant_id;
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status
            FROM accounts_receivable ar WHERE ar.id = p_ar_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_list_accounts_payable(
            p_tenant_id UUID, p_status VARCHAR DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC,
            balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status
            FROM accounts_payable ap
            WHERE ap.tenant_id = p_tenant_id
              AND (p_status IS NULL OR ap.status = p_status)
            ORDER BY ap.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_create_accounts_payable(
            p_tenant_id UUID, p_bill_number VARCHAR, p_vendor_name VARCHAR,
            p_amount NUMERIC, p_due_date TEXT, p_description TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR,
            vendor_name VARCHAR, description TEXT, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_payable (
                id, tenant_id, bill_number, vendor_name, description,
                amount, amount_paid, balance, due_date, status, created_at
            )
            VALUES (
                new_id, p_tenant_id, p_bill_number, p_vendor_name,
                p_description, p_amount, 0, p_amount, p_due_date::DATE,
                'pending', NOW()
            );
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status
            FROM accounts_payable ap WHERE ap.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE FUNCTION fn_record_ap_payment(
            p_tenant_id UUID, p_ap_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR,
            vendor_name VARCHAR, description TEXT, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR
        ) AS $$
        BEGIN
            UPDATE accounts_payable ap2
            SET amount_paid = ap2.amount_paid + p_amount,
                balance = ap2.balance - p_amount,
                status = CASE WHEN ap2.balance - p_amount <= 0 THEN 'paid'
                              ELSE 'partial' END
            WHERE ap2.id = p_ap_id AND ap2.tenant_id = p_tenant_id;
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status
            FROM accounts_payable ap WHERE ap.id = p_ap_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # fn_financial_dashboard: back to business-wide AR/AP
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_financial_dashboard(
            p_tenant_id UUID, p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            cash_balance NUMERIC, outstanding_receivable NUMERIC,
            outstanding_payable NUMERIC, total_expenses_this_month NUMERIC,
            expense_by_category JSONB
        ) AS $$
        DECLARE
            cash_acct UUID;
            month_start TIMESTAMPTZ := DATE_TRUNC('month', NOW());
            result JSONB := '{}'::JSONB;
        BEGIN
            SELECT id INTO cash_acct FROM chart_of_accounts
            WHERE tenant_id = p_tenant_id AND code = '1000' LIMIT 1;

            SELECT COALESCE(
                (SELECT jsonb_object_agg(sub.category, sub.total)
                 FROM (
                    SELECT e.category, SUM(e.amount) AS total
                    FROM expenses e
                    WHERE e.tenant_id = p_tenant_id
                      AND e.expense_date >= month_start
                      AND (p_store_id IS NULL OR e.store_id = p_store_id)
                    GROUP BY e.category
                 ) sub),
                '{}'::JSONB
            ) INTO result;

            RETURN QUERY
            SELECT
                COALESCE((SELECT SUM(je.debit) - SUM(je.credit)
                    FROM journal_entries je
                    JOIN journals j ON j.id = je.journal_id
                    WHERE je.account_id = cash_acct AND je.status = 'posted'
                      AND (p_store_id IS NULL OR j.store_id = p_store_id)
                ), 0) AS cash_balance,
                COALESCE((SELECT SUM(ar.balance) FROM accounts_receivable ar
                    WHERE ar.tenant_id = p_tenant_id
                      AND ar.status IN ('pending','overdue','partial')
                ), 0) AS outstanding_receivable,
                COALESCE((SELECT SUM(ap.balance) FROM accounts_payable ap
                    WHERE ap.tenant_id = p_tenant_id
                      AND ap.status IN ('pending','overdue','partial')
                ), 0) AS outstanding_payable,
                COALESCE((SELECT SUM(e.amount) FROM expenses e
                    WHERE e.tenant_id = p_tenant_id
                      AND e.expense_date >= month_start
                      AND (p_store_id IS NULL OR e.store_id = p_store_id)
                ), 0) AS total_expenses_this_month,
                result AS expense_by_category;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 2. Drop columns + indexes ────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS idx_accounts_receivable_tenant_store")
    op.execute("DROP INDEX IF EXISTS idx_accounts_payable_tenant_store")
    op.execute("ALTER TABLE accounts_receivable DROP COLUMN IF EXISTS store_id")
    op.execute("ALTER TABLE accounts_payable DROP COLUMN IF EXISTS store_id")
