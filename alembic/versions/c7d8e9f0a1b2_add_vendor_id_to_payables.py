"""add vendor_id to accounts_payable

Recording a payable stored the vendor's name as free text, so a bill could
not be traced back to the vendor record the way a receivable already is
(accounts_receivable.customer_id). With customers now typed as customer or
vendor, the payable sheet can offer a vendor picker — but the picked vendor
has to be stored, not just their name.

The column is nullable: existing payables keep their free-text vendor_name,
and a manually typed vendor still works.

Revision ID: c7d8e9f0a1b2
Revises: b9c8d7e6f5a4
"""

from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b9c8d7e6f5a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE accounts_payable ADD COLUMN IF NOT EXISTS vendor_id UUID")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_accounts_payable_tenant_vendor "
        "ON accounts_payable(tenant_id, vendor_id)"
    )

    # Link what we can: match the stored vendor_name against a vendor record
    # in the same tenant. Ambiguous or unmatched names stay null rather than
    # being linked to the wrong vendor.
    op.execute("""
        UPDATE accounts_payable ap
        SET vendor_id = sub.vendor_id
        FROM (
            SELECT DISTINCT ON (p.id)
                   p.id AS payable_id,
                   c.id AS vendor_id
            FROM accounts_payable p
            JOIN customers c
              ON c.tenant_id = p.tenant_id
             AND c.type = 'vendor'
             AND lower(btrim(p.vendor_name)) = lower(btrim(c.name))
            WHERE p.vendor_id IS NULL
              AND p.vendor_name IS NOT NULL
            ORDER BY p.id, c.created_at
        ) AS sub
        WHERE ap.id = sub.payable_id
    """)

    # fn_create_accounts_payable accepts and returns the vendor link. Written
    # here rather than by editing b9c8d7e6f5a4 because that one has already
    # been applied.
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "fn_create_accounts_payable(uuid,varchar,varchar,numeric,text,text,uuid,varchar,uuid,uuid)"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_create_accounts_payable(
            p_tenant_id UUID, p_bill_number VARCHAR, p_vendor_name VARCHAR,
            p_amount NUMERIC, p_due_date TEXT, p_description TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL, p_expense_category VARCHAR DEFAULT 'other',
            p_created_by UUID DEFAULT NULL, p_vendor_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            out_id UUID, out_tenant_id UUID, bill_number VARCHAR,
            vendor_name VARCHAR, description TEXT, amount NUMERIC,
            amount_paid NUMERIC, balance NUMERIC, due_date TIMESTAMPTZ,
            status VARCHAR, store_id UUID, out_vendor_id UUID
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
                id, tenant_id, bill_number, vendor_id, vendor_name, description,
                amount, amount_paid, balance, due_date, status, created_at,
                store_id, expense_category
            )
            VALUES (
                new_id, p_tenant_id, p_bill_number, p_vendor_id, p_vendor_name,
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
                   ap.due_date, ap.status, ap.store_id, ap.vendor_id
            FROM accounts_payable ap WHERE ap.id = new_id;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "fn_create_accounts_payable(uuid,varchar,varchar,numeric,text,text,uuid,varchar,uuid,uuid)"
    )
    op.execute("DROP INDEX IF EXISTS idx_accounts_payable_tenant_vendor")
    op.execute("ALTER TABLE accounts_payable DROP COLUMN IF EXISTS vendor_id")