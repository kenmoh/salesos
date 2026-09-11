"""add_accounting_rpc_functions

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-09-11

"""
from alembic import op


revision = "o3p4q5r6s7t8"
down_revision = "n2o3p4q5r6s7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill posted_at on journal_entries from their parent journal
    op.execute("""
        UPDATE journal_entries je
        SET posted_at = j.posted_at
        FROM journals j
        WHERE je.journal_id = j.id AND je.posted_at IS NULL
    """)

    # ── fn_list_accounts (match AccountResponse) ──────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_accounts(p_tenant_id UUID)
        RETURNS TABLE (
            id UUID, tenant_id UUID, code VARCHAR, name VARCHAR,
            account_type VARCHAR, status VARCHAR
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT c.id, c.tenant_id, c.code, c.name, c.account_type, c.status
            FROM chart_of_accounts c
            WHERE c.tenant_id = p_tenant_id
            ORDER BY c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_create_account ─────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_account(
            p_tenant_id UUID, p_code VARCHAR, p_name VARCHAR,
            p_account_type VARCHAR, p_parent_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, code VARCHAR, name VARCHAR,
            account_type VARCHAR, parent_id UUID, status VARCHAR,
            created_at TIMESTAMPTZ
        ) AS $$
        DECLARE
            new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO chart_of_accounts (id, tenant_id, code, name, account_type, parent_id, status, created_at)
            VALUES (new_id, p_tenant_id, p_code, p_name, p_account_type, p_parent_id, 'active', NOW());

            RETURN QUERY
            SELECT c.id, c.tenant_id, c.code, c.name, c.account_type,
                   c.parent_id, c.status, c.created_at
            FROM chart_of_accounts c WHERE c.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_post_journal ───────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_post_journal(
            p_tenant_id UUID, p_uid UUID, p_desc TEXT,
            p_entries JSONB, p_ref_id UUID DEFAULT NULL, p_ref_type VARCHAR DEFAULT NULL
        ) RETURNS UUID AS $$
        DECLARE
            j_id UUID := gen_random_uuid();
            j_number VARCHAR;
            entry JSONB;
            total_debit NUMERIC := 0;
            total_credit NUMERIC := 0;
        BEGIN
            j_number := 'JRN-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || UPPER(SUBSTR(j_id::TEXT, 1, 8));

            FOR entry IN SELECT * FROM jsonb_array_elements(p_entries)
            LOOP
                total_debit := total_debit + COALESCE((entry->>'debit')::NUMERIC, 0);
                total_credit := total_credit + COALESCE((entry->>'credit')::NUMERIC, 0);
            END LOOP;

            IF ABS(total_debit - total_credit) > 0.01 THEN
                RAISE EXCEPTION 'Debits (%) must equal credits (%)', total_debit, total_credit;
            END IF;

            INSERT INTO journals (id, tenant_id, journal_number, description, reference_id, reference_type, status, posted_by, posted_at, created_at)
            VALUES (j_id, p_tenant_id, j_number, p_desc, p_ref_id, p_ref_type, 'posted', p_uid, NOW(), NOW());

            FOR entry IN SELECT * FROM jsonb_array_elements(p_entries)
            LOOP
                INSERT INTO journal_entries (id, journal_id, tenant_id, account_id, account_code, debit, credit, description, type, status, posted_at, amount)
                VALUES (
                    gen_random_uuid(), j_id, p_tenant_id,
                    (entry->>'account_id')::UUID,
                    entry->>'account_code',
                    COALESCE((entry->>'debit')::NUMERIC, 0),
                    COALESCE((entry->>'credit')::NUMERIC, 0),
                    entry->>'description',
                    COALESCE(entry->>'type', 'other'),
                    'posted', NOW(),
                    COALESCE((entry->>'debit')::NUMERIC, 0) + COALESCE((entry->>'credit')::NUMERIC, 0)
                );
            END LOOP;

            RETURN j_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_list_journals (match JournalListItem) ──────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_journals(
            p_tenant_id UUID, p_limit INT DEFAULT 50, p_offset INT DEFAULT 0
        ) RETURNS TABLE (
            id UUID, journal_number VARCHAR, description TEXT, status VARCHAR,
            created_at TIMESTAMPTZ, entry_count BIGINT,
            total_debit NUMERIC, total_credit NUMERIC
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT j.id, j.journal_number, j.description, j.status, j.created_at,
                   COUNT(je.id) AS entry_count,
                   COALESCE(SUM(je.debit), 0) AS total_debit,
                   COALESCE(SUM(je.credit), 0) AS total_credit
            FROM journals j
            LEFT JOIN journal_entries je ON je.journal_id = j.id
            WHERE j.tenant_id = p_tenant_id
            GROUP BY j.id, j.journal_number, j.description, j.status, j.created_at
            ORDER BY j.created_at DESC
            LIMIT p_limit OFFSET p_offset;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_trial_balance (match TrialBalanceItem) ─────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_trial_balance(
            p_tenant_id UUID, p_at TEXT DEFAULT NULL
        ) RETURNS TABLE (
            account_id UUID, account_code VARCHAR, account_name VARCHAR,
            account_type VARCHAR, debit NUMERIC, credit NUMERIC
        ) AS $$
        DECLARE cutoff TIMESTAMPTZ;
        BEGIN
            cutoff := CASE WHEN p_at IS NOT NULL THEN (p_at::DATE + INTERVAL '1 day')::TIMESTAMPTZ ELSE NOW() END;
            RETURN QUERY
            SELECT c.id, c.code, c.name, c.account_type,
                   COALESCE(SUM(je.debit), 0) AS debit, COALESCE(SUM(je.credit), 0) AS credit
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je ON je.account_id = c.id
                AND je.status = 'posted' AND (je.posted_at IS NULL OR je.posted_at <= cutoff)
            WHERE c.tenant_id = p_tenant_id
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING COALESCE(SUM(je.debit), 0) > 0 OR COALESCE(SUM(je.credit), 0) > 0
            ORDER BY c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_profit_and_loss (match PnLLineItem - no account_type) ──────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_profit_and_loss(
            p_tenant_id UUID, p_from TEXT, p_to TEXT
        ) RETURNS TABLE (
            account_id UUID, account_code VARCHAR, account_name VARCHAR, amount NUMERIC
        ) AS $$
        DECLARE cutoff TIMESTAMPTZ;
        BEGIN
            cutoff := (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ;
            RETURN QUERY
            SELECT c.id, c.code, c.name,
                   CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END AS amount
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je ON je.account_id = c.id
                AND je.status = 'posted' AND (je.posted_at IS NULL OR (je.posted_at >= p_from::DATE AND je.posted_at <= cutoff))
            WHERE c.tenant_id = p_tenant_id AND c.account_type IN ('revenue', 'expense')
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END != 0
            ORDER BY c.account_type, c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_list_accounts_receivable (match ReceivableResponse) ────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_accounts_receivable(
            p_tenant_id UUID, p_status VARCHAR DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status
            FROM accounts_receivable ar
            WHERE ar.tenant_id = p_tenant_id AND (p_status IS NULL OR ar.status = p_status)
            ORDER BY ar.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_create_accounts_receivable (match ReceivableResponse) ──────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_accounts_receivable(
            p_tenant_id UUID, p_customer_id UUID, p_customer_name VARCHAR,
            p_invoice_number VARCHAR, p_amount NUMERIC, p_due_date DATE,
            p_invoice_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_receivable (id, tenant_id, invoice_id, customer_id, customer_name, invoice_number, amount, amount_paid, balance, due_date, status, created_at)
            VALUES (new_id, p_tenant_id, p_invoice_id, p_customer_id, p_customer_name, p_invoice_number, p_amount, 0, p_amount, p_due_date::TIMESTAMPTZ, 'pending', NOW());
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance, ar.due_date, ar.status
            FROM accounts_receivable ar WHERE ar.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_record_ar_payment (match ReceivableResponse) ───────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_record_ar_payment(
            p_tenant_id UUID, p_ar_id UUID, p_amount NUMERIC,
            p_payment_date DATE, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, customer_id UUID,
            customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        BEGIN
            UPDATE accounts_receivable
            SET amount_paid = amount_paid + p_amount, balance = balance - p_amount,
                status = CASE WHEN balance - p_amount <= 0 THEN 'paid' ELSE 'partial' END
            WHERE id = p_ar_id AND tenant_id = p_tenant_id;
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance, ar.due_date, ar.status
            FROM accounts_receivable ar WHERE ar.id = p_ar_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_list_accounts_payable (match PayableResponse) ──────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_accounts_payable(
            p_tenant_id UUID, p_status VARCHAR DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC,
            balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name, ap.description,
                   ap.amount, ap.amount_paid, ap.balance, ap.due_date, ap.status
            FROM accounts_payable ap
            WHERE ap.tenant_id = p_tenant_id AND (p_status IS NULL OR ap.status = p_status)
            ORDER BY ap.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_create_accounts_payable (match PayableResponse) ────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_accounts_payable(
            p_tenant_id UUID, p_bill_number VARCHAR, p_vendor_name VARCHAR,
            p_amount NUMERIC, p_due_date DATE, p_description TEXT DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC,
            balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_payable (id, tenant_id, bill_number, vendor_name, description, amount, amount_paid, balance, due_date, status, created_at)
            VALUES (new_id, p_tenant_id, p_bill_number, p_vendor_name, p_description, p_amount, 0, p_amount, p_due_date::TIMESTAMPTZ, 'pending', NOW());
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name, ap.description,
                   ap.amount, ap.amount_paid, ap.balance, ap.due_date, ap.status
            FROM accounts_payable ap WHERE ap.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_record_ap_payment (match PayableResponse) ──────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_record_ap_payment(
            p_tenant_id UUID, p_ap_id UUID, p_amount NUMERIC,
            p_payment_date DATE, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC,
            balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        BEGIN
            UPDATE accounts_payable
            SET amount_paid = amount_paid + p_amount, balance = balance - p_amount,
                status = CASE WHEN balance - p_amount <= 0 THEN 'paid' ELSE 'partial' END
            WHERE id = p_ap_id AND tenant_id = p_tenant_id;
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name, ap.description,
                   ap.amount, ap.amount_paid, ap.balance, ap.due_date, ap.status
            FROM accounts_payable ap WHERE ap.id = p_ap_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_list_expenses (match ExpenseResponse) ──────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_expenses(
            p_tenant_id UUID, p_category VARCHAR DEFAULT NULL,
            p_from TEXT DEFAULT NULL, p_to TEXT DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, expense_number VARCHAR, category VARCHAR,
            description TEXT, amount NUMERIC, expense_date TIMESTAMPTZ, vendor VARCHAR,
            receipt_url TEXT, account_id UUID, journal_id UUID, created_by UUID
        ) AS $$
        DECLARE from_ts TIMESTAMPTZ; to_ts TIMESTAMPTZ;
        BEGIN
            from_ts := CASE WHEN p_from IS NOT NULL THEN p_from::DATE::TIMESTAMPTZ ELSE NULL END;
            to_ts := CASE WHEN p_to IS NOT NULL THEN (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ ELSE NULL END;
            RETURN QUERY
            SELECT e.id, e.tenant_id, e.expense_number, e.category, e.description,
                   e.amount, e.expense_date, e.vendor, e.receipt_url, e.account_id,
                   e.journal_id, e.created_by
            FROM expenses e
            WHERE e.tenant_id = p_tenant_id
              AND (p_category IS NULL OR e.category = p_category)
              AND (from_ts IS NULL OR e.expense_date >= from_ts)
              AND (to_ts IS NULL OR e.expense_date < to_ts)
            ORDER BY e.expense_date DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_create_expense (match ExpenseResponse) ─────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_expense(
            p_tenant_id UUID, p_category VARCHAR, p_description TEXT,
            p_amount NUMERIC, p_expense_date TEXT, p_created_by UUID,
            p_vendor VARCHAR DEFAULT NULL, p_receipt_url TEXT DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, expense_number VARCHAR, category VARCHAR,
            description TEXT, amount NUMERIC, expense_date TIMESTAMPTZ, vendor VARCHAR,
            receipt_url TEXT, account_id UUID, journal_id UUID, created_by UUID
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
            exp_number VARCHAR := 'EXP-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || UPPER(SUBSTR(new_id::TEXT, 1, 8));
            exp_account_code VARCHAR; exp_account_id UUID; cash_account_id UUID; new_journal_id UUID;
        BEGIN
            exp_account_code := CASE p_category WHEN 'rent' THEN '5010' WHEN 'utilities' THEN '5020' WHEN 'salaries' THEN '5030' WHEN 'supplies' THEN '5040' WHEN 'transport' THEN '5050' WHEN 'marketing' THEN '5060' WHEN 'bank_charges' THEN '5070' ELSE '5099' END;
            SELECT id INTO exp_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = exp_account_code LIMIT 1;
            SELECT id INTO cash_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = '1000' LIMIT 1;
            new_journal_id := fn_post_journal(p_tenant_id := p_tenant_id, p_uid := p_created_by, p_desc := 'Expense: ' || p_category || ' - ' || p_description,
                p_entries := jsonb_build_array(
                    jsonb_build_object('account_id', exp_account_id::TEXT, 'account_code', exp_account_code, 'debit', p_amount, 'credit', 0, 'description', p_description, 'type', 'expense'),
                    jsonb_build_object('account_id', cash_account_id::TEXT, 'account_code', '1000', 'debit', 0, 'credit', p_amount, 'description', p_description, 'type', 'asset')
                ));
            INSERT INTO expenses (id, tenant_id, expense_number, category, description, amount, vendor, receipt_url, expense_date, account_id, journal_id, created_by, created_at)
            VALUES (new_id, p_tenant_id, exp_number, p_category, p_description, p_amount, p_vendor, p_receipt_url, p_expense_date::DATE, exp_account_id, new_journal_id, p_created_by, NOW());
            RETURN QUERY
            SELECT e.id, e.tenant_id, e.expense_number, e.category, e.description,
                   e.amount, e.expense_date, e.vendor, e.receipt_url, e.account_id, e.journal_id, e.created_by
            FROM expenses e WHERE e.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_expense_summary ────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_expense_summary(
            p_tenant_id UUID, p_from TEXT DEFAULT NULL, p_to TEXT DEFAULT NULL
        ) RETURNS TABLE (category VARCHAR, total NUMERIC) AS $$
        BEGIN
            RETURN QUERY
            SELECT e.category, SUM(e.amount) AS total
            FROM expenses e
            WHERE e.tenant_id = p_tenant_id
              AND (p_from IS NULL OR e.expense_date >= p_from::DATE)
              AND (p_to IS NULL OR e.expense_date <= p_to::DATE)
            GROUP BY e.category
            ORDER BY total DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_financial_dashboard (match FinancialDashboardResponse) ─────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_financial_dashboard(
            p_tenant_id UUID
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
                    WHERE e.tenant_id = p_tenant_id AND e.expense_date >= month_start
                    GROUP BY e.category
                 ) sub),
                '{}'::JSONB
            ) INTO result;

            RETURN QUERY
            SELECT
                COALESCE((SELECT SUM(je.debit) - SUM(je.credit)
                    FROM journal_entries je WHERE je.account_id = cash_acct AND je.status = 'posted'), 0) AS cash_balance,
                COALESCE((SELECT SUM(ar.balance) FROM accounts_receivable ar
                    WHERE ar.tenant_id = p_tenant_id AND ar.status IN ('pending','overdue','partial')), 0) AS outstanding_receivable,
                COALESCE((SELECT SUM(ap.balance) FROM accounts_payable ap
                    WHERE ap.tenant_id = p_tenant_id AND ap.status IN ('pending','overdue','partial')), 0) AS outstanding_payable,
                COALESCE((SELECT SUM(e.amount) FROM expenses e
                    WHERE e.tenant_id = p_tenant_id AND e.expense_date >= month_start), 0) AS total_expenses_this_month,
                result AS expense_by_category;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    for fn in [
        "fn_financial_dashboard", "fn_expense_summary", "fn_create_expense",
        "fn_list_expenses", "fn_record_ap_payment", "fn_create_accounts_payable",
        "fn_list_accounts_payable", "fn_record_ar_payment", "fn_create_accounts_receivable",
        "fn_list_accounts_receivable", "fn_profit_and_loss", "fn_trial_balance",
        "fn_list_journals", "fn_post_journal", "fn_create_account", "fn_list_accounts",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID, ...) CASCADE;")
