"""fix expense account codes in fn_create_expense

The original fn_create_expense mapped expense categories to account codes
5010-5099, but the seeded Chart of Accounts (app/accounting/seed.py) uses
5100-5900 (rent=5100, utilities=5200, salaries=5300, ... misc=5900). The
account lookup therefore returned NULL and journal_entries.account_id hit
its NOT NULL constraint with:
    asyncpg.exceptions.NotNullViolationError: null value in column
    "account_id" of relation "journal_entries"

This migration:
1. Recreates fn_create_expense with seed-consistent codes matching
   EXPENSE_CATEGORY_ACCOUNT_MAP, and raises a descriptive exception when
   the expense or cash account is missing instead of inserting NULLs.
2. Hardens fn_post_journal to reject entries with a NULL/missing
   account_id with a clear error message.

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Create Date: 2026-09-20
"""

from alembic import op


revision = "q6r7s8t9u0v1"
down_revision = "p5q6r7s8t9u0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── fn_post_journal: guard against NULL account_id ────────────────────
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
    """
    )

    # asyncpg rejects multiple SQL commands per prepared statement, so the
    # DROP and CREATE for fn_create_expense run as separate statements.
    op.execute(
        "DROP FUNCTION IF EXISTS fn_create_expense(uuid,varchar,text,numeric,text,uuid,varchar,text) CASCADE;"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_create_expense(
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


def downgrade() -> None:
    # Restore the original (buggy) fn_create_expense with 50xx codes.
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_expense(
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
            exp_account_code := CASE p_category WHEN 'rent' THEN '5010' WHEN 'utilities' THEN '5020' WHEN 'salaries' THEN '5030' WHEN 'supplies' THEN '5040' WHEN 'transport' THEN '5050' WHEN 'marketing' THEN '5060' WHEN 'bank_charges' THEN '5070' ELSE '5099' END;
            SELECT id INTO exp_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = exp_account_code LIMIT 1;
            SELECT id INTO cash_account_id FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND code = '1000' LIMIT 1;
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
