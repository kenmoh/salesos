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
                RAISE EXCEPTION 'Expense account ''%'' not found for tenant %. Seed the Chart of Accounts first.', exp_account_code, p_tenant_id;
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



# TRANSACTION RESPONSE
"""
{
  "status": "success",
  "message": "Transaction fetched successfully",
  "data": {
    "id": 288200108,
    "tx_ref": "LiveCardTest",
    "flw_ref": "YemiDesola/FLW275407301",
    "device_fingerprint": "N/A",
    "amount": 100,
    "currency": "NGN",
    "charged_amount": 100,
    "app_fee": 1.4,
    "merchant_fee": 0,
    "processor_response": "Approved by Financial Institution",
    "auth_model": "PIN",
    "ip": "::ffff:10.5.179.3",
    "narration": "CARD Transaction ",
    "status": "successful",
    "payment_type": "card",
    "created_at": "2020-07-15T14:31:16.000Z",
    "account_id": 17321,
    "card": {
      "first_6digits": "232343",
      "last_4digits": "4567",
      "issuer": "FIRST CITY MONUMENT BANK PLC",
      "country": "NIGERIA NG",
      "type": "VERVE",
      "token": "flw-t1nf-4676a40c7ddf5f12scr432aa12d471973-k3n",
      "expiry": "02/23"
    },
    "meta": null,
    "amount_settled": 98.6,
    "customer": {
      "id": 216519823,
      "name": "Yemi Desola",
      "phone_number": "N/A",
      "email": "user@gmail.com",
      "created_at": "2020-07-15T14:31:15.000Z"
    }
  }
}
"""

# PAYMENT
"""
curl --request POST \
     --url https://api.flutterwave.com/v3/payments \
     --header 'Authorization: Bearer FLWSECK_TEST-SANDBOXDEMOKEY-X' \
     --header 'Content-Type: application/json' \
     --header 'accept: application/json' \
     --data '
{
  "amount": 1000,
  "tx_ref": "YOUR_PAYMENT_REFERENCE",
  "currency": "NGN",
  "redirect_url": "https://www.flutterwave.com/ng",
  "customer": {
    "email": "user@example.com",
    "phone_number": "08012345612",
    "name": "Mike John"
  },
  "customizations": {
    "title": "Web Demo Payment",
    "logo": "http://www.piedpiper.com/app/themes/joystick-v27/images/logo.png"
  },
  "configuration": {
    "session_duration": 30,
    "max_retry_attempt": 5
  },
  "payment_plan": 3807,
  "payment_options": "card, ussd, mobilemoneyghana",
  "link_expiration": "2024-02-14T12:20:00",
  "meta": {
    "meta_1": "meta_value",
    "meta_2": "meta_value"
  }
}
'
"""

# PAYMENT RESPONSE

"""
{
  "status": "success",
  "message": "Hosted Link",
  "data": {
    "link": "https://checkout.flutterwave.com/v3/hosted/pay/flwlnk-01j4dc4maegqf82pfqg4chw46b"
  }
}
"""

"""
TRANSFER
curl --request POST \
     --url 'https://api.flutterwave.com/v3/charges?type=bank_transfer' \
     --header 'Authorization: Bearer FLWSECK_TEST-SANDBOXDEMOKEY-X' \
     --header 'Content-Type: application/json' \
     --header 'accept: application/json' \
     --data '
{
  "amount": 100,
  "email": "user@flw.com",
  "currency": "NGN",
  "tx_ref": "MC-MC-1585230ew9v5050e8",
  "fullname": "Yemi Desola",
  "phone_number": "07033002245",
  "client_ip": "154.123.220.1",
  "device_fingerprint": "62wd23423rq324323qew1",
  "is_permanent": false,
  "bank_code": "090567"
}
'
"""

# TRANSFER RESPONSE
"""
{
  "status": "success",
  "message": "Charge initiated",
  "meta": {
    "authorization": {
      "transfer_reference": "ZZSS4548178111081675127069",
      "transfer_account": "9755152912",
      "transfer_bank": "Flutterwave MFB",
      "account_expiration": "2020-03-27 09:53:39",
      "transfer_note": "Please make a bank transfer to Yemi Desola",
      "transfer_amount": 1500,
      "mode": "banktransfer"
    }
  }
}
"""