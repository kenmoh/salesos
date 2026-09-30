"""add store_id to journals and expenses for per-store accounting

Accounting was entirely business-wide: journals, journal_entries and
expenses carried no store attribution, so P&L/TB/CF/BS/journals could not
be filtered per store. Sales already know their store (sales.store_id),
so sale-linked journals can be backfilled; expenses/manual journals get a
nullable store_id going forward (NULL = business-wide/untagged).

Changes:
1. journals.store_id / expenses.store_id (nullable UUID) + indexes.
2. Backfill: sale / sale_void / sale_return / split_adjustment journals
   inherit the store of the sale they reference.
3. Recreate statement fns with an optional p_store_id filter
   (NULL = all stores, the previous behaviour):
   fn_profit_and_loss, fn_trial_balance, fn_list_journals,
   fn_list_expenses (also returns store_id now), fn_expense_summary,
   fn_financial_dashboard.
4. fn_post_journal / fn_create_expense gain p_store_id so new journals
   and expenses are tagged at creation.

Postgres cannot change a function's signature with CREATE OR REPLACE,
so each old overload is dropped first. asyncpg rejects multiple commands
per prepared statement, hence one op.execute per statement.

Revision ID: y4z5a6b7c8d9
Revises: x3y4z5a6b7c8
"""

from alembic import op

revision = "y4z5a6b7c8d9"
down_revision = "x3y4z5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Columns + indexes ─────────────────────────────────────────────
    op.execute("ALTER TABLE journals ADD COLUMN store_id UUID")
    op.execute("ALTER TABLE expenses ADD COLUMN store_id UUID")
    op.execute(
        "CREATE INDEX idx_journals_tenant_store ON journals(tenant_id, store_id)"
    )
    op.execute(
        "CREATE INDEX idx_expenses_tenant_store ON expenses(tenant_id, store_id)"
    )

    # ── 2. Backfill from the referenced sale ─────────────────────────────
    op.execute("""
        UPDATE journals j
        SET store_id = s.store_id
        FROM sales s
        WHERE j.reference_type IN ('sale', 'sale_void', 'sale_return', 'split_adjustment')
          AND j.reference_id = s.id
          AND j.store_id IS NULL
    """)

    # ── 3. Drop old overloads (signature change) ─────────────────────────
    for sig in [
        "fn_profit_and_loss(uuid, text, text)",
        "fn_trial_balance(uuid, text)",
        "fn_list_journals(uuid, integer, integer)",
        "fn_list_expenses(uuid, varchar, text, text)",
        "fn_expense_summary(uuid, text, text)",
        "fn_financial_dashboard(uuid)",
        "fn_post_journal(uuid, uuid, text, jsonb, uuid, varchar)",
        "fn_create_expense(uuid, varchar, text, numeric, text, uuid, varchar, text)",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {sig};")

    # ── fn_profit_and_loss: optional per-store revenue/expense split ─────
    op.execute("""
        CREATE FUNCTION fn_profit_and_loss(
            p_tenant_id UUID, p_from TEXT, p_to TEXT, p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            account_id UUID, account_code VARCHAR, account_name VARCHAR,
            account_type VARCHAR, amount NUMERIC
        ) AS $$
        DECLARE cutoff TIMESTAMPTZ;
        BEGIN
            cutoff := (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ;
            RETURN QUERY
            SELECT c.id, c.code, c.name, c.account_type,
                   CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END AS amount
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je ON je.account_id = c.id
                AND je.status = 'posted' AND (je.posted_at IS NULL OR (je.posted_at >= p_from::DATE AND je.posted_at <= cutoff))
            LEFT JOIN journals j ON j.id = je.journal_id
            WHERE c.tenant_id = p_tenant_id AND c.account_type IN ('revenue', 'expense')
              AND (p_store_id IS NULL OR j.store_id = p_store_id)
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END != 0
            ORDER BY c.account_type, c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_trial_balance: optional per-store balances ────────────────────
    op.execute("""
        CREATE FUNCTION fn_trial_balance(
            p_tenant_id UUID, p_at TEXT DEFAULT NULL, p_store_id UUID DEFAULT NULL
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
            LEFT JOIN journals j ON j.id = je.journal_id
            WHERE c.tenant_id = p_tenant_id
              AND (p_store_id IS NULL OR j.store_id = p_store_id)
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING COALESCE(SUM(je.debit), 0) > 0 OR COALESCE(SUM(je.credit), 0) > 0
            ORDER BY c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_list_journals: optional per-store filter ──────────────────────
    op.execute("""
        CREATE FUNCTION fn_list_journals(
            p_tenant_id UUID, p_limit INT DEFAULT 50, p_offset INT DEFAULT 0,
            p_store_id UUID DEFAULT NULL
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
              AND (p_store_id IS NULL OR j.store_id = p_store_id)
            GROUP BY j.id, j.journal_number, j.description, j.status, j.created_at
            ORDER BY j.created_at DESC
            LIMIT p_limit OFFSET p_offset;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_list_expenses: optional per-store filter; returns store_id ────
    op.execute("""
        CREATE FUNCTION fn_list_expenses(
            p_tenant_id UUID, p_category VARCHAR DEFAULT NULL,
            p_from TEXT DEFAULT NULL, p_to TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            id UUID, tenant_id UUID, expense_number VARCHAR, category VARCHAR,
            description TEXT, amount NUMERIC, expense_date TIMESTAMPTZ, vendor VARCHAR,
            receipt_url TEXT, account_id UUID, journal_id UUID, created_by UUID,
            store_id UUID
        ) AS $$
        DECLARE from_ts TIMESTAMPTZ; to_ts TIMESTAMPTZ;
        BEGIN
            from_ts := CASE WHEN p_from IS NOT NULL THEN p_from::DATE::TIMESTAMPTZ ELSE NULL END;
            to_ts := CASE WHEN p_to IS NOT NULL THEN (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ ELSE NULL END;
            RETURN QUERY
            SELECT e.id, e.tenant_id, e.expense_number, e.category, e.description,
                   e.amount, e.expense_date, e.vendor, e.receipt_url, e.account_id,
                   e.journal_id, e.created_by, e.store_id
            FROM expenses e
            WHERE e.tenant_id = p_tenant_id
              AND (p_category IS NULL OR e.category = p_category)
              AND (from_ts IS NULL OR e.expense_date >= from_ts)
              AND (to_ts IS NULL OR e.expense_date < to_ts)
              AND (p_store_id IS NULL OR e.store_id = p_store_id)
            ORDER BY e.expense_date DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_expense_summary: optional per-store filter ────────────────────
    op.execute("""
        CREATE FUNCTION fn_expense_summary(
            p_tenant_id UUID, p_from TEXT DEFAULT NULL, p_to TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (category VARCHAR, total NUMERIC) AS $$
        BEGIN
            RETURN QUERY
            SELECT e.category, SUM(e.amount) AS total
            FROM expenses e
            WHERE e.tenant_id = p_tenant_id
              AND (p_from IS NULL OR e.expense_date >= p_from::DATE)
              AND (p_to IS NULL OR e.expense_date <= p_to::DATE)
              AND (p_store_id IS NULL OR e.store_id = p_store_id)
            GROUP BY e.category
            ORDER BY total DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_financial_dashboard: per-store cash + expenses (AR/AP stay ────
    #    business-wide — invoices carry no store)
    op.execute("""
        CREATE FUNCTION fn_financial_dashboard(
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
                    WHERE e.tenant_id = p_tenant_id AND e.expense_date >= month_start
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
                      AND (p_store_id IS NULL OR j.store_id = p_store_id)), 0) AS cash_balance,
                COALESCE((SELECT SUM(ar.balance) FROM accounts_receivable ar
                    WHERE ar.tenant_id = p_tenant_id AND ar.status IN ('pending','overdue','partial')), 0) AS outstanding_receivable,
                COALESCE((SELECT SUM(ap.balance) FROM accounts_payable ap
                    WHERE ap.tenant_id = p_tenant_id AND ap.status IN ('pending','overdue','partial')), 0) AS outstanding_payable,
                COALESCE((SELECT SUM(e.amount) FROM expenses e
                    WHERE e.tenant_id = p_tenant_id AND e.expense_date >= month_start
                      AND (p_store_id IS NULL OR e.store_id = p_store_id)), 0) AS total_expenses_this_month,
                result AS expense_by_category;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── fn_post_journal: tag the journal with its store ──────────────────
    op.execute("""
        CREATE FUNCTION fn_post_journal(
            p_tenant_id UUID, p_uid UUID, p_desc TEXT,
            p_entries JSONB, p_ref_id UUID DEFAULT NULL, p_ref_type VARCHAR DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
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

            INSERT INTO journals (id, tenant_id, journal_number, description, reference_id, reference_type, status, posted_by, posted_at, store_id, created_at)
            VALUES (j_id, p_tenant_id, j_number, p_desc, p_ref_id, p_ref_type, 'posted', p_uid, NOW(), p_store_id, NOW());

            FOR entry IN SELECT * FROM jsonb_array_elements(p_entries)
            LOOP
                IF COALESCE(entry->>'account_id', '') = '' THEN
                    RAISE EXCEPTION 'Journal entry "%" has no account_id (account_code=%). Seed the Chart of Accounts for tenant % and resolve the account before posting.',
                        COALESCE(entry->>'description', ''), COALESCE(entry->>'account_code', ''), p_tenant_id;
                END IF;

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

    # ── fn_create_expense: tag expense + its journal with the store ──────
    op.execute("""
        CREATE FUNCTION fn_create_expense(
            p_tenant_id UUID, p_category VARCHAR, p_description TEXT,
            p_amount NUMERIC, p_expense_date TEXT, p_created_by UUID,
            p_vendor VARCHAR DEFAULT NULL, p_receipt_url TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, expense_number VARCHAR, category VARCHAR,
            description TEXT, amount NUMERIC, expense_date TIMESTAMPTZ, vendor VARCHAR,
            receipt_url TEXT, account_id UUID, journal_id UUID, created_by UUID
        ) AS $$
        DECLARE v_id UUID := gen_random_uuid();
            exp_number VARCHAR := 'EXP-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || UPPER(SUBSTR(v_id::TEXT, 1, 8));
            exp_account_code VARCHAR; exp_account_id UUID; cash_account_id UUID; new_journal_id UUID;
        BEGIN
            -- Codes mirror EXPENSE_CATEGORY_ACCOUNT_MAP in app/accounting/seed.py
            -- (rent=5100, utilities=5200, salaries=5300, supplies=5400, transport=5500,
            --  marketing=5600, bank_charges=5700, phone_internet=5800, other=5900)
            exp_account_code := CASE p_category
                WHEN 'rent' THEN '5100'
                WHEN 'utilities' THEN '5200'
                WHEN 'salaries' THEN '5300'
                WHEN 'supplies' THEN '5400'
                WHEN 'transport' THEN '5500'
                WHEN 'marketing' THEN '5600'
                WHEN 'bank_charges' THEN '5700'
                WHEN 'phone_internet' THEN '5800'
                ELSE '5900'
            END;

            SELECT id INTO exp_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = exp_account_code LIMIT 1;
            IF exp_account_id IS NULL THEN
                RAISE EXCEPTION 'Expense account ''%'' not found for tenant %. Seed the Chart of Accounts first (categories map: rent=5100, utilities=5200, salaries=5300, supplies=5400, transport=5500, marketing=5600, bank_charges=5700, phone_internet=5800, other=5900).',
                    exp_account_code, p_tenant_id;
            END IF;

            SELECT id INTO cash_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = '1000' LIMIT 1;
            IF cash_account_id IS NULL THEN
                RAISE EXCEPTION 'Cash account ''1000'' not found for tenant %. Seed the Chart of Accounts first.', p_tenant_id;
            END IF;

            new_journal_id := fn_post_journal(p_tenant_id := p_tenant_id, p_uid := p_created_by, p_desc := 'Expense: ' || p_category || ' - ' || p_description,
                p_entries := jsonb_build_array(
                    jsonb_build_object('account_id', exp_account_id::TEXT, 'account_code', exp_account_code, 'debit', p_amount, 'credit', 0, 'description', p_description, 'type', 'expense'),
                    jsonb_build_object('account_id', cash_account_id::TEXT, 'account_code', '1000', 'debit', 0, 'credit', p_amount, 'description', p_description, 'type', 'asset')
                ),
                p_store_id := p_store_id);

            INSERT INTO expenses (id, tenant_id, expense_number, category, description, amount, vendor, receipt_url, expense_date, account_id, journal_id, store_id, created_by, created_at)
            VALUES (v_id, p_tenant_id, exp_number, p_category, p_description, p_amount, p_vendor, p_receipt_url, p_expense_date::DATE, exp_account_id, new_journal_id, p_store_id, p_created_by, NOW());

            RETURN QUERY
            SELECT ex.id, ex.tenant_id, ex.expense_number, ex.category, ex.description,
                   ex.amount, ex.expense_date, ex.vendor, ex.receipt_url, ex.account_id, ex.journal_id, ex.created_by
            FROM expenses ex WHERE ex.id = v_id;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # ── Restore old function overloads (no p_store_id) ───────────────────
    for sig in [
        "fn_profit_and_loss(uuid, text, text, uuid)",
        "fn_trial_balance(uuid, text, uuid)",
        "fn_list_journals(uuid, integer, integer, uuid)",
        "fn_list_expenses(uuid, varchar, text, text, uuid)",
        "fn_expense_summary(uuid, text, text, uuid)",
        "fn_financial_dashboard(uuid, uuid)",
        "fn_post_journal(uuid, uuid, text, jsonb, uuid, varchar, uuid)",
        "fn_create_expense(uuid, varchar, text, numeric, text, uuid, varchar, text, uuid)",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {sig};")

    op.execute("""
        CREATE FUNCTION fn_profit_and_loss(
            p_tenant_id UUID, p_from TEXT, p_to TEXT
        ) RETURNS TABLE (
            account_id UUID, account_code VARCHAR, account_name VARCHAR,
            account_type VARCHAR, amount NUMERIC
        ) AS $$
        DECLARE cutoff TIMESTAMPTZ;
        BEGIN
            cutoff := (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ;
            RETURN QUERY
            SELECT c.id, c.code, c.name, c.account_type,
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

    op.execute("""
        CREATE FUNCTION fn_trial_balance(
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

    op.execute("""
        CREATE FUNCTION fn_list_journals(
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

    op.execute("""
        CREATE FUNCTION fn_list_expenses(
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

    op.execute("""
        CREATE FUNCTION fn_expense_summary(
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

    op.execute("""
        CREATE FUNCTION fn_financial_dashboard(
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

    op.execute("""
        CREATE FUNCTION fn_post_journal(
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
                IF COALESCE(entry->>'account_id', '') = '' THEN
                    RAISE EXCEPTION 'Journal entry "%" has no account_id (account_code=%). Seed the Chart of Accounts for tenant % and resolve the account before posting.',
                        COALESCE(entry->>'description', ''), COALESCE(entry->>'account_code', ''), p_tenant_id;
                END IF;

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

    op.execute("""
        CREATE FUNCTION fn_create_expense(
            p_tenant_id UUID, p_category VARCHAR, p_description TEXT,
            p_amount NUMERIC, p_expense_date TEXT, p_created_by UUID,
            p_vendor VARCHAR DEFAULT NULL, p_receipt_url TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, expense_number VARCHAR, category VARCHAR,
            description TEXT, amount NUMERIC, expense_date TIMESTAMPTZ, vendor VARCHAR,
            receipt_url TEXT, account_id UUID, journal_id UUID, created_by UUID
        ) AS $$
        DECLARE v_id UUID := gen_random_uuid();
            exp_number VARCHAR := 'EXP-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || UPPER(SUBSTR(v_id::TEXT, 1, 8));
            exp_account_code VARCHAR; exp_account_id UUID; cash_account_id UUID; new_journal_id UUID;
        BEGIN
            exp_account_code := CASE p_category
                WHEN 'rent' THEN '5100'
                WHEN 'utilities' THEN '5200'
                WHEN 'salaries' THEN '5300'
                WHEN 'supplies' THEN '5400'
                WHEN 'transport' THEN '5500'
                WHEN 'marketing' THEN '5600'
                WHEN 'bank_charges' THEN '5700'
                WHEN 'phone_internet' THEN '5800'
                ELSE '5900'
            END;

            SELECT id INTO exp_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = exp_account_code LIMIT 1;
            IF exp_account_id IS NULL THEN
                RAISE EXCEPTION 'Expense account ''%'' not found for tenant %. Seed the Chart of Accounts first (categories map: rent=5100, utilities=5200, salaries=5300, supplies=5400, transport=5500, marketing=5600, bank_charges=5700, phone_internet=5800, other=5900).',
                    exp_account_code, p_tenant_id;
            END IF;

            SELECT id INTO cash_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = '1000' LIMIT 1;
            IF cash_account_id IS NULL THEN
                RAISE EXCEPTION 'Cash account ''1000'' not found for tenant %. Seed the Chart of Accounts first.', p_tenant_id;
            END IF;

            new_journal_id := fn_post_journal(p_tenant_id := p_tenant_id, p_uid := p_created_by, p_desc := 'Expense: ' || p_category || ' - ' || p_description,
                p_entries := jsonb_build_array(
                    jsonb_build_object('account_id', exp_account_id::TEXT, 'account_code', exp_account_code, 'debit', p_amount, 'credit', 0, 'description', p_description, 'type', 'expense'),
                    jsonb_build_object('account_id', cash_account_id::TEXT, 'account_code', '1000', 'debit', 0, 'credit', p_amount, 'description', p_description, 'type', 'asset')
                ));

            INSERT INTO expenses (id, tenant_id, expense_number, category, description, amount, vendor, receipt_url, expense_date, account_id, journal_id, created_by, created_at)
            VALUES (v_id, p_tenant_id, exp_number, p_category, p_description, p_amount, p_vendor, p_receipt_url, p_expense_date::DATE, exp_account_id, new_journal_id, p_created_by, NOW());

            RETURN QUERY
            SELECT ex.id, ex.tenant_id, ex.expense_number, ex.category, ex.description,
                   ex.amount, ex.expense_date, ex.vendor, ex.receipt_url, ex.account_id, ex.journal_id, ex.created_by
            FROM expenses ex WHERE ex.id = v_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── Drop the store columns ───────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS idx_journals_tenant_store")
    op.execute("DROP INDEX IF EXISTS idx_expenses_tenant_store")
    op.execute("ALTER TABLE journals DROP COLUMN IF EXISTS store_id")
    op.execute("ALTER TABLE expenses DROP COLUMN IF EXISTS store_id")
