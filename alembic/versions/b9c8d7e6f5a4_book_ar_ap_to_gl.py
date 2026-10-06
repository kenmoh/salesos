"""book receivables and payables into the general ledger

Recording a receivable or a payable wrote only a sub-ledger row. The
general-ledger account behind them (1100 Receivable, 2000 Payable) was never
debited or credited, so:

- paying a manually created receivable credited 1100 with no matching debit,
  and the matching revenue was never recognised;
- paying a manually created payable debited 2000 with no matching credit, and
  the expense or asset the vendor billed for was never booked at all.

Account 2000 ended up with a debit balance and 1100 with a credit balance --
impossible for either side of the balance sheet -- and the equity plug
(equity := assets - liabilities) absorbed the error so the balance check
still read zero.

This migration:

1. Adds accounts_payable.expense_category so the counterpart of a payable can
   be booked. The account code is resolved server-side from the same category
   map the expense endpoint uses, so a client cannot point a payable at an
   arbitrary account.
2. fn_create_accounts_receivable posts Dr 1100 / Cr 4000.
3. fn_create_accounts_payable posts Dr <category account> / Cr 2000.
   Both tag the journal with reference_type 'receivable'/'payable' so the
   backfill below can tell booked rows from unbooked ones.
4. Backfills the opening journals for every receivable and payable that
   exists without one, dated at the row's created_at so history is preserved.
   Running it twice is a no-op.

Revision ID: b9c8d7e6f5a4
Revises: f6a7b8c9d0e1
"""

from alembic import op

revision = "b9c8d7e6f5a4"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Payables record what they are for ──────────────────────────────
    op.execute("ALTER TABLE accounts_payable ADD COLUMN IF NOT EXISTS expense_category VARCHAR(50)")
    op.execute(
        "UPDATE accounts_payable SET expense_category = 'other' "
        "WHERE expense_category IS NULL"
    )

    # ── 2/3. Creation books a balanced journal ─────────────────────────────
    op.execute("DROP FUNCTION IF EXISTS fn_create_accounts_receivable(uuid,uuid,varchar,varchar,numeric,text,uuid,uuid)")
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_accounts_receivable(
            p_tenant_id UUID, p_customer_id UUID, p_customer_name VARCHAR,
            p_invoice_number VARCHAR, p_amount NUMERIC, p_due_date TEXT,
            p_invoice_id UUID DEFAULT NULL, p_store_id UUID DEFAULT NULL,
            p_created_by UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, out_customer_id UUID,
            out_customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        DECLARE
            new_id UUID := gen_random_uuid();
            v_ar_id UUID;
            v_receivable_id UUID;
            v_revenue_id UUID;
        BEGIN
            v_ar_id := COALESCE(p_created_by, gen_random_uuid());

            SELECT id INTO v_receivable_id FROM chart_of_accounts
            WHERE tenant_id = p_tenant_id AND code = '1100' LIMIT 1;
            SELECT id INTO v_revenue_id FROM chart_of_accounts
            WHERE tenant_id = p_tenant_id AND code = '4000' LIMIT 1;

            IF v_receivable_id IS NULL OR v_revenue_id IS NULL THEN
                RAISE EXCEPTION
                    'Receivable account 1100 or revenue account 4000 is missing for tenant %. Seed the Chart of Accounts before recording receivables.',
                    p_tenant_id;
            END IF;

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

            -- Dr 1100 Receivable / Cr 4000 Revenue: the invoice is revenue
            -- earned now and a receivable until it is collected.
            PERFORM fn_post_journal(
                p_tenant_id := p_tenant_id,
                p_uid := v_ar_id,
                p_desc := 'Receivable: ' || p_invoice_number,
                p_entries := jsonb_build_array(
                    jsonb_build_object(
                        'account_id', v_receivable_id::TEXT, 'account_code', '1100',
                        'debit', p_amount, 'credit', 0,
                        'description', 'Receivable: ' || p_invoice_number,
                        'type', 'asset'),
                    jsonb_build_object(
                        'account_id', v_revenue_id::TEXT, 'account_code', '4000',
                        'debit', 0, 'credit', p_amount,
                        'description', 'Revenue: ' || p_invoice_number,
                        'type', 'revenue')
                ),
                p_ref_id := new_id,
                p_ref_type := 'receivable',
                p_store_id := p_store_id
            );

            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance,
                   ar.due_date, ar.status, ar.store_id
            FROM accounts_receivable ar WHERE ar.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("DROP FUNCTION IF EXISTS fn_create_accounts_payable(uuid,varchar,varchar,numeric,text,text,uuid)")
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_accounts_payable(
            p_tenant_id UUID, p_bill_number VARCHAR, p_vendor_name VARCHAR,
            p_amount NUMERIC, p_due_date TEXT, p_description TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL, p_expense_category VARCHAR DEFAULT 'other',
            p_created_by UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR,
            vendor_name VARCHAR, description TEXT, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID
        ) AS $$
        DECLARE
            new_id UUID := gen_random_uuid();
            v_ap_id UUID;
            v_payable_id UUID;
            v_expense_code VARCHAR;
            v_expense_id UUID;
        BEGIN
            v_ap_id := COALESCE(p_created_by, gen_random_uuid());

            -- Same category map as fn_create_expense (see app/accounting/seed.py).
            v_expense_code := CASE COALESCE(p_expense_category, 'other')
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

            SELECT id INTO v_payable_id FROM chart_of_accounts
            WHERE tenant_id = p_tenant_id AND code = '2000' LIMIT 1;
            SELECT id INTO v_expense_id FROM chart_of_accounts
            WHERE tenant_id = p_tenant_id AND code = v_expense_code LIMIT 1;

            IF v_payable_id IS NULL OR v_expense_id IS NULL THEN
                RAISE EXCEPTION
                    'Payable account 2000 or expense account % is missing for tenant %. Seed the Chart of Accounts before recording payables.',
                    v_expense_code, p_tenant_id;
            END IF;

            INSERT INTO accounts_payable (
                id, tenant_id, bill_number, vendor_name, description,
                amount, amount_paid, balance, due_date, status, created_at,
                store_id, expense_category
            )
            VALUES (
                new_id, p_tenant_id, p_bill_number, p_vendor_name,
                p_description, p_amount, 0, p_amount, p_due_date::DATE,
                'pending', NOW(), p_store_id, COALESCE(p_expense_category, 'other')
            );

            -- Dr <expense> / Cr 2000 Payable: the liability is recognised when
            -- the bill arrives, not when it is paid.
            PERFORM fn_post_journal(
                p_tenant_id := p_tenant_id,
                p_uid := v_ap_id,
                p_desc := 'Payable: ' || p_bill_number,
                p_entries := jsonb_build_array(
                    jsonb_build_object(
                        'account_id', v_expense_id::TEXT, 'account_code', v_expense_code,
                        'debit', p_amount, 'credit', 0,
                        'description', COALESCE(p_description, p_bill_number),
                        'type', 'expense'),
                    jsonb_build_object(
                        'account_id', v_payable_id::TEXT, 'account_code', '2000',
                        'debit', 0, 'credit', p_amount,
                        'description', COALESCE(p_description, p_bill_number),
                        'type', 'liability')
                ),
                p_ref_id := new_id,
                p_ref_type := 'payable',
                p_store_id := p_store_id
            );

            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name,
                   ap.description, ap.amount, ap.amount_paid, ap.balance,
                   ap.due_date, ap.status, ap.store_id
            FROM accounts_payable ap WHERE ap.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 4. Backfill opening journals for rows booked without one ──────────
    # Receivables: Dr 1100 / Cr 4000, dated at the receivable's created_at.
    op.execute("""
        INSERT INTO journals (
            id, tenant_id, journal_number, description, reference_id,
            reference_type, status, posted_by, posted_at, store_id, created_at
        )
        SELECT
            gen_random_uuid(),
            ar.tenant_id,
            'JRN-' || TO_CHAR(COALESCE(ar.created_at, NOW()), 'YYYYMMDD')
                   || '-OPEN' || UPPER(SUBSTRING(ar.id::TEXT, 1, 4)),
            'Receivable: ' || ar.invoice_number || ' (opening entry)',
            ar.id,
            'receivable',
            'posted',
            COALESCE(ar.customer_id, gen_random_uuid()),
            COALESCE(ar.created_at, NOW()),
            ar.store_id,
            COALESCE(ar.created_at, NOW())
        FROM accounts_receivable ar
        JOIN chart_of_accounts recv ON recv.tenant_id = ar.tenant_id AND recv.code = '1100'
        JOIN chart_of_accounts rev  ON rev.tenant_id  = ar.tenant_id AND rev.code  = '4000'
        WHERE NOT EXISTS (
            SELECT 1 FROM journals j
            WHERE j.reference_type = 'receivable'
              AND j.reference_id = ar.id
              AND j.tenant_id = ar.tenant_id
        )
    """)

    op.execute("""
        INSERT INTO journal_entries (
            id, journal_id, tenant_id, account_id, account_code,
            debit, credit, description, type, status, posted_at, amount
        )
        SELECT
            gen_random_uuid(),
            j.id,
            j.tenant_id,
            recv.id,
            '1100',
            ar.amount,
            0,
            'Receivable: ' || ar.invoice_number,
            'asset',
            'posted',
            j.posted_at,
            ar.amount
        FROM accounts_receivable ar
        JOIN journals j
          ON j.reference_type = 'receivable'
         AND j.reference_id = ar.id
         AND j.tenant_id = ar.tenant_id
         AND j.description LIKE '%(opening entry)'
        JOIN chart_of_accounts recv ON recv.tenant_id = ar.tenant_id AND recv.code = '1100'
    """)

    op.execute("""
        INSERT INTO journal_entries (
            id, journal_id, tenant_id, account_id, account_code,
            debit, credit, description, type, status, posted_at, amount
        )
        SELECT
            gen_random_uuid(),
            j.id,
            j.tenant_id,
            rev.id,
            '4000',
            0,
            ar.amount,
            'Revenue: ' || ar.invoice_number,
            'revenue',
            'posted',
            j.posted_at,
            ar.amount
        FROM accounts_receivable ar
        JOIN journals j
          ON j.reference_type = 'receivable'
         AND j.reference_id = ar.id
         AND j.tenant_id = ar.tenant_id
         AND j.description LIKE '%(opening entry)'
        JOIN chart_of_accounts rev ON rev.tenant_id = ar.tenant_id AND rev.code = '4000'
    """)

    # Payables: Dr <category account> / Cr 2000, dated at the payable's created_at.
    op.execute("""
        INSERT INTO journals (
            id, tenant_id, journal_number, description, reference_id,
            reference_type, status, posted_by, posted_at, store_id, created_at
        )
        SELECT
            gen_random_uuid(),
            ap.tenant_id,
            'JRN-' || TO_CHAR(COALESCE(ap.created_at, NOW()), 'YYYYMMDD')
                   || '-OPEN' || UPPER(SUBSTRING(ap.id::TEXT, 1, 4)),
            'Payable: ' || ap.bill_number || ' (opening entry)',
            ap.id,
            'payable',
            'posted',
            COALESCE((SELECT c.id FROM chart_of_accounts c
                      WHERE c.tenant_id = ap.tenant_id AND c.code = '2000' LIMIT 1),
                     gen_random_uuid()),
            COALESCE(ap.created_at, NOW()),
            ap.store_id,
            COALESCE(ap.created_at, NOW())
        FROM accounts_payable ap
        JOIN chart_of_accounts pay ON pay.tenant_id = ap.tenant_id AND pay.code = '2000'
        WHERE NOT EXISTS (
            SELECT 1 FROM journals j
            WHERE j.reference_type = 'payable'
              AND j.reference_id = ap.id
              AND j.tenant_id = ap.tenant_id
        )
    """)

    op.execute("""
        INSERT INTO journal_entries (
            id, journal_id, tenant_id, account_id, account_code,
            debit, credit, description, type, status, posted_at, amount
        )
        SELECT
            gen_random_uuid(),
            j.id,
            j.tenant_id,
            pay.id,
            '2000',
            0,
            ap.amount,
            COALESCE(ap.description, ap.bill_number),
            'liability',
            'posted',
            j.posted_at,
            ap.amount
        FROM accounts_payable ap
        JOIN journals j
          ON j.reference_type = 'payable'
         AND j.reference_id = ap.id
         AND j.tenant_id = ap.tenant_id
         AND j.description LIKE '%(opening entry)'
        JOIN chart_of_accounts pay ON pay.tenant_id = ap.tenant_id AND pay.code = '2000'
    """)

    op.execute("""
        INSERT INTO journal_entries (
            id, journal_id, tenant_id, account_id, account_code,
            debit, credit, description, type, status, posted_at, amount
        )
        SELECT
            gen_random_uuid(),
            j.id,
            j.tenant_id,
            exp.id,
            exp.code,
            ap.amount,
            0,
            COALESCE(ap.description, ap.bill_number),
            'expense',
            'posted',
            j.posted_at,
            ap.amount
        FROM accounts_payable ap
        JOIN journals j
          ON j.reference_type = 'payable'
         AND j.reference_id = ap.id
         AND j.tenant_id = ap.tenant_id
         AND j.description LIKE '%(opening entry)'
        JOIN chart_of_accounts exp
          ON exp.tenant_id = ap.tenant_id
         AND exp.code = CASE COALESCE(ap.expense_category, 'other')
                WHEN 'rent' THEN '5100'
                WHEN 'utilities' THEN '5200'
                WHEN 'salaries' THEN '5300'
                WHEN 'supplies' THEN '5400'
                WHEN 'transport' THEN '5500'
                WHEN 'marketing' THEN '5600'
                WHEN 'bank_charges' THEN '5700'
                WHEN 'phone_internet' THEN '5800'
                ELSE '5900'
            END
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE accounts_payable DROP COLUMN IF EXISTS expense_category")

    # Remove the backfilled opening journals; rows booked by the new functions
    # keep the same reference_type, so only the '(opening entry)' ones go.
    op.execute("""
        DELETE FROM journal_entries
        WHERE journal_id IN (
            SELECT id FROM journals WHERE description LIKE '%(opening entry)'
        )
    """)
    op.execute("DELETE FROM journals WHERE description LIKE '%(opening entry)'")

    op.execute(
        "DROP FUNCTION IF EXISTS "
        "fn_create_accounts_receivable(uuid,uuid,varchar,varchar,numeric,text,uuid,uuid,uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "fn_create_accounts_payable(uuid,varchar,varchar,numeric,text,text,uuid,varchar,uuid)"
    )