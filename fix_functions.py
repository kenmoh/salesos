"""Temporary script to fix all accounting stored procedures.
All params use TEXT for dates; all ambiguous columns use out_ prefix.
"""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = "postgresql+asyncpg://neondb_owner:npg_Vyp9Ezb0kYQt@ep-cool-field-aeqkjz0t-pooler.c-2.us-east-2.aws.neon.tech/neondb?ssl=require"

FUNCTIONS = [
    (
        "fn_create_accounts_receivable",
        "DROP FUNCTION IF EXISTS fn_create_accounts_receivable(uuid,uuid,varchar,varchar,numeric,date,uuid) CASCADE",
        "DROP FUNCTION IF EXISTS fn_create_accounts_receivable(uuid,uuid,varchar,varchar,numeric,text,uuid) CASCADE",
        """CREATE OR REPLACE FUNCTION fn_create_accounts_receivable(
            p_tenant_id UUID, p_customer_id UUID, p_customer_name VARCHAR,
            p_invoice_number VARCHAR, p_amount NUMERIC, p_due_date TEXT,
            p_invoice_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, out_customer_id UUID,
            out_customer_name VARCHAR, invoice_number VARCHAR, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_receivable (id, tenant_id, invoice_id, customer_id, customer_name,
                invoice_number, amount, amount_paid, balance, due_date, status, created_at)
            VALUES (new_id, p_tenant_id, p_invoice_id, p_customer_id, p_customer_name,
                p_invoice_number, p_amount, 0, p_amount, p_due_date::DATE, 'pending', NOW());
            RETURN QUERY
            SELECT ar.id, ar.tenant_id, ar.customer_id, ar.customer_name,
                   ar.invoice_number, ar.amount, ar.amount_paid, ar.balance, ar.due_date, ar.status
            FROM accounts_receivable ar WHERE ar.id = new_id;
        END;
        $$ LANGUAGE plpgsql;""",
    ),
    (
        "fn_record_ar_payment",
        "DROP FUNCTION IF EXISTS fn_record_ar_payment(uuid,uuid,numeric,date,text) CASCADE",
        "DROP FUNCTION IF EXISTS fn_record_ar_payment(uuid,uuid,numeric,text,text) CASCADE",
        """CREATE OR REPLACE FUNCTION fn_record_ar_payment(
            p_tenant_id UUID, p_ar_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, customer_id UUID,
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
        $$ LANGUAGE plpgsql;""",
    ),
    (
        "fn_create_accounts_payable",
        "DROP FUNCTION IF EXISTS fn_create_accounts_payable(uuid,varchar,varchar,numeric,date,text) CASCADE",
        "DROP FUNCTION IF EXISTS fn_create_accounts_payable(uuid,varchar,varchar,numeric,text,text) CASCADE",
        """CREATE OR REPLACE FUNCTION fn_create_accounts_payable(
            p_tenant_id UUID, p_bill_number VARCHAR, p_vendor_name VARCHAR,
            p_amount NUMERIC, p_due_date TEXT, p_description TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
            description TEXT, amount NUMERIC, amount_paid NUMERIC,
            balance NUMERIC, due_date TIMESTAMPTZ, status VARCHAR
        ) AS $$
        DECLARE new_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO accounts_payable (id, tenant_id, bill_number, vendor_name, description,
                amount, amount_paid, balance, due_date, status, created_at)
            VALUES (new_id, p_tenant_id, p_bill_number, p_vendor_name, p_description,
                p_amount, 0, p_amount, p_due_date::DATE, 'pending', NOW());
            RETURN QUERY
            SELECT ap.id, ap.tenant_id, ap.bill_number, ap.vendor_name, ap.description,
                   ap.amount, ap.amount_paid, ap.balance, ap.due_date, ap.status
            FROM accounts_payable ap WHERE ap.id = new_id;
        END;
        $$ LANGUAGE plpgsql;""",
    ),
    (
        "fn_record_ap_payment",
        "DROP FUNCTION IF EXISTS fn_record_ap_payment(uuid,uuid,numeric,date,text) CASCADE",
        "DROP FUNCTION IF EXISTS fn_record_ap_payment(uuid,uuid,numeric,text,text) CASCADE",
        """CREATE OR REPLACE FUNCTION fn_record_ap_payment(
            p_tenant_id UUID, p_ap_id UUID, p_amount NUMERIC,
            p_payment_date TEXT, p_notes TEXT DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR, vendor_name VARCHAR,
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
        $$ LANGUAGE plpgsql;""",
    ),
    (
        "fn_create_expense",
        "DROP FUNCTION IF EXISTS fn_create_expense(uuid,varchar,text,numeric,text,uuid,varchar,text) CASCADE",
        None,
        """CREATE OR REPLACE FUNCTION fn_create_expense(
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
        $$ LANGUAGE plpgsql;""",
    ),
]


async def main():
    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"server_settings": {"statement_timeout": "30000"}},
    )

    for name, *drop_sqls, create_sql in FUNCTIONS:
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as s:
                async with s.begin():
                    for ds in drop_sqls:
                        if ds:
                            await s.execute(text(ds))
                    await s.execute(text(create_sql))
                    await s.commit()
                    print(f"OK  {name}")
        except Exception as e:
            print(f"FAIL {name}: {e}")

    await engine.dispose()


asyncio.run(main())
