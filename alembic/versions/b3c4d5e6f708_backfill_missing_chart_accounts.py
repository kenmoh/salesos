"""backfill chart of accounts the template gained later

The default chart already listed VAT Payable, and every new tenant is seeded
from it. Tenants provisioned before that line was added never received it,
because nothing revisited their chart -- and a sale journal credits VAT to 2300,
so the first taxed sale for such a tenant has no liability account to credit
against.

Rather than patch one code, this inserts every template account a tenant is
missing. Anything added to the template from now on can be backfilled the same
way, and tenants keep whatever accounts they already have.

The values are generated from DEFAULT_ACCOUNTS so this cannot drift from the
template it mirrors.

Revision ID: b3c4d5e6f708
Revises: f0a1b2c3d4e5
"""

from alembic import op

revision = "b3c4d5e6f708"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None

# The default chart, as DEFAULT_ACCOUNTS defines it:
#  1000   Cash
#  1010   Bank Account
#  1100   Accounts Receivable
#  1200   Inventory
#  1300   Prepaid Expenses
#  1500   Equipment
#  2000   Accounts Payable
#  2100   Loans Payable
#  2200   Unearned Revenue
#  2300   VAT Payable
#  3000   Owner's Capital
#  3100   Retained Earnings
#  4000   Sales Revenue
#  4100   Service Revenue
#  4200   Other Income
#  5000   Cost of Goods Sold
#  5100   Rent
#  5200   Utilities
#  5300   Salaries
#  5400   Supplies
#  5500   Transportation
#  5600   Marketing
#  5700   Bank Charges
#  5800   Phone & Internet
#  5900   Miscellaneous

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
        # Idempotent per tenant and per code: a tenant that already has the
        # account is left alone, so re-running inserts nothing.
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


def downgrade() -> None:
    # An account a tenant's ledger already references is not ours to remove, and
    # removing 2300 would unbalance every sale journal that credits it.
    raise NotImplementedError(
        "removing accounts a tenant's ledger may already reference is not safe; "
        "roll forward instead"
    )
