"""product taxes become a list, and tax gains its own liability account

Three changes that belong together, because they are the same idea: a tax is
identified by its name, and a product decides which of them apply to it.

Products carried a single tax_id, which cannot express a good that is both
VAT and excise. It becomes tax_ids, backfilled from the old column, and the
column itself is dropped so nothing can keep writing the single-tax form.

Taxes carry no account of their own, so every tax booked to 2300 VAT Payable
regardless of what it was. account_code is added and filled in now: VAT by
name goes to 2300, everything else to the new 2400 Other Taxes Payable, which
this migration also creates for every tenant the same way b3c4d5e6f708 did.

Sale and document lines gain tax_breakdown to hold the snapshot of what each
line was actually taxed at. Rates change and taxes get renamed; a receipt
printed last month must not change with them.

Revision ID: c4d5e6f70819
Revises: b3c4d5e6f708
"""

from alembic import op

revision = "c4d5e6f70819"
down_revision = "b3c4d5e6f708"
branch_labels = None
depends_on = None

# DEFAULT_ACCOUNTS as app/accounting/seed.py defines it, including the
# 2400 this migration introduces.
TEMPLATE = [
    ('1000', 'Cash', 'asset'),
    ('1010', 'Bank Account', 'asset'),
    ('1100', 'Accounts Receivable', 'asset'),
    ('1200', 'Inventory', 'asset'),
    ('1300', 'Prepaid Expenses', 'asset'),
    ('1500', 'Equipment', 'asset'),
    ('2000', 'Accounts Payable', 'liability'),
    ('2100', 'Loans Payable', 'liability'),
    ('2200', 'Unearned Revenue', 'liability'),
    ('2300', 'VAT Payable', 'liability'),
    ('2400', 'Other Taxes Payable', 'liability'),
    ('3000', "Owner's Capital", 'equity'),
    ('3100', 'Retained Earnings', 'equity'),
    ('4000', 'Sales Revenue', 'revenue'),
    ('4100', 'Service Revenue', 'revenue'),
    ('4200', 'Other Income', 'revenue'),
    ('5000', 'Cost of Goods Sold', 'expense'),
    ('5100', 'Rent', 'expense'),
    ('5200', 'Utilities', 'expense'),
    ('5300', 'Salaries', 'expense'),
    ('5400', 'Supplies', 'expense'),
    ('5500', 'Transportation', 'expense'),
    ('5600', 'Marketing', 'expense'),
    ('5700', 'Bank Charges', 'expense'),
    ('5800', 'Phone & Internet', 'expense'),
    ('5900', 'Miscellaneous', 'expense'),
]


def upgrade() -> None:
    for code, name, account_type in TEMPLATE:
        op.execute(
            """
            INSERT INTO chart_of_accounts
                (id, tenant_id, code, name, account_type, status, created_at)
            SELECT gen_random_uuid(), t.id, '{code}', '{name}', '{account_type}',
                   'active', now()
            FROM tenants t
            WHERE NOT EXISTS (
                SELECT 1 FROM chart_of_accounts a
                WHERE a.tenant_id = t.id AND a.code = '{code}'
            )
            """.format(code=code, name=name.replace("'", "''"),
                       account_type=account_type)
        )

    op.execute("ALTER TABLE taxes ADD COLUMN account_code VARCHAR(10)")
    # The name is the only thing that identifies a tax, so it is the only thing
    # to go on -- and only here, once, while the value is still editable.
    op.execute(
        """
        UPDATE taxes
        SET account_code = CASE
            WHEN regexp_replace(lower(name), '[^a-z]', '', 'g')
                 IN ('vat', 'valueaddedtax', 'vatpayable') THEN '2300'
            ELSE '2400'
        END
        WHERE account_code IS NULL
        """
    )

    op.execute("ALTER TABLE products ADD COLUMN tax_ids JSON")
    op.execute(
        """
        UPDATE products
        SET tax_ids = CASE
            WHEN tax_id IS NULL THEN '[]'::json
            ELSE json_build_array(tax_id::text)
        END
        """
    )
    op.execute("ALTER TABLE products ALTER COLUMN tax_ids SET DEFAULT '[]'::json")
    op.execute("ALTER TABLE products ALTER COLUMN tax_ids SET NOT NULL")
    # Dropping the single-value column is the point: leaving it in place means
    # the next writer silently reverts a product to one tax.
    op.execute("ALTER TABLE products DROP COLUMN tax_id")

    op.execute("ALTER TABLE sale_items ADD COLUMN tax_breakdown JSON")
    op.execute("ALTER TABLE document_items ADD COLUMN tax_breakdown JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE document_items DROP COLUMN tax_breakdown")
    op.execute("ALTER TABLE sale_items DROP COLUMN tax_breakdown")

    op.execute("ALTER TABLE products ADD COLUMN tax_id UUID")
    op.execute(
        """
        UPDATE products
        SET tax_id = (tax_ids ->> 0)::uuid
        WHERE tax_ids IS NOT NULL AND json_array_length(tax_ids) > 0
        """
    )
    op.execute("ALTER TABLE products DROP COLUMN tax_ids")

    op.execute("ALTER TABLE taxes DROP COLUMN account_code")

    # 2400 stays: a ledger that has already credited it is not ours to unwind,
    # which is the same reason b3c4d5e6f708 left its accounts alone.
