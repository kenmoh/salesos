"""Default Chart of Accounts seed data for Nigerian small businesses.

This module provides a pre-configured set of financial accounts that are
automatically created when a new business tenant is onboarded. The accounts
follow Nigerian small business accounting conventions and cover all major
financial categories: assets, liabilities, equity, revenue, and expenses.

Abbreviations Used in This Module
----------------------------------
- COA: Chart of Accounts -- the complete list of all financial accounts.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- COGS: Cost of Goods Sold -- the direct cost of products sold by the business.
- FK: Foreign Key -- a reference from one table to another.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.

Account Code Conventions:
    - 1xxx: Asset accounts (things the business owns)
    - 2xxx: Liability accounts (things the business owes)
    - 3xxx: Equity accounts (owner's stake in the business)
    - 4xxx: Revenue accounts (money earned from sales)
    - 5xxx: Expense accounts (money spent on operations)
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .models import ChartOfAccount
from .repository import create_account, get_account_by_code
from .schemas import ChartOfAccountCreateCommand


# ═══════════════════════════════════════════════════════════════════════════════
#  DEFAULT ACCOUNT DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_ACCOUNTS: list[dict[str, str]] = [
    # ─────────────────────────────────────────────────────────────────────────
    #  ASSET ACCOUNTS (1xxx)
    #  Assets are resources owned by the business that have future economic value.
    #  Debit = increase asset, Credit = decrease asset.
    # ─────────────────────────────────────────────────────────────────────────
    {
        "code": "1000",
        "name": "Cash",
        "account_type": "asset",
    },
    {
        "code": "1010",
        "name": "Bank Account",
        "account_type": "asset",
    },
    {
        "code": "1100",
        "name": "Accounts Receivable",
        "account_type": "asset",
    },
    {
        "code": "1200",
        "name": "Inventory",
        "account_type": "asset",
    },
    {
        "code": "1300",
        "name": "Prepaid Expenses",
        "account_type": "asset",
    },
    {
        "code": "1500",
        "name": "Equipment",
        "account_type": "asset",
    },
    # ─────────────────────────────────────────────────────────────────────────
    #  LIABILITY ACCOUNTS (2xxx)
    #  Liabilities are obligations the business owes to external parties.
    #  Debit = decrease liability, Credit = increase liability.
    # ─────────────────────────────────────────────────────────────────────────
    {
        "code": "2000",
        "name": "Accounts Payable",
        "account_type": "liability",
    },
    {
        "code": "2100",
        "name": "Loans Payable",
        "account_type": "liability",
    },
    {
        "code": "2200",
        "name": "Unearned Revenue",
        "account_type": "liability",
    },
    # ─────────────────────────────────────────────────────────────────────────
    #  EQUITY ACCOUNTS (3xxx)
    #  Equity represents the owner's claim on the business assets.
    #  Debit = decrease equity, Credit = increase equity.
    # ─────────────────────────────────────────────────────────────────────────
    {
        "code": "3000",
        "name": "Owner's Capital",
        "account_type": "equity",
    },
    {
        "code": "3100",
        "name": "Retained Earnings",
        "account_type": "equity",
    },
    # ─────────────────────────────────────────────────────────────────────────
    #  REVENUE ACCOUNTS (4xxx)
    #  Revenue is income earned from selling goods or providing services.
    #  Debit = decrease revenue, Credit = increase revenue.
    # ─────────────────────────────────────────────────────────────────────────
    {
        "code": "4000",
        "name": "Sales Revenue",
        "account_type": "revenue",
    },
    {
        "code": "4100",
        "name": "Service Revenue",
        "account_type": "revenue",
    },
    {
        "code": "4200",
        "name": "Other Income",
        "account_type": "revenue",
    },
    # ─────────────────────────────────────────────────────────────────────────
    #  EXPENSE ACCOUNTS (5xxx)
    #  Expenses are costs incurred to operate the business.
    #  Debit = increase expense, Credit = decrease expense.
    # ─────────────────────────────────────────────────────────────────────────
    {
        "code": "5000",
        "name": "Cost of Goods Sold",
        "account_type": "expense",
    },
    {
        "code": "5100",
        "name": "Rent",
        "account_type": "expense",
    },
    {
        "code": "5200",
        "name": "Utilities",
        "account_type": "expense",
    },
    {
        "code": "5300",
        "name": "Salaries",
        "account_type": "expense",
    },
    {
        "code": "5400",
        "name": "Supplies",
        "account_type": "expense",
    },
    {
        "code": "5500",
        "name": "Transportation",
        "account_type": "expense",
    },
    {
        "code": "5600",
        "name": "Marketing",
        "account_type": "expense",
    },
    {
        "code": "5700",
        "name": "Bank Charges",
        "account_type": "expense",
    },
    {
        "code": "5800",
        "name": "Phone & Internet",
        "account_type": "expense",
    },
    {
        "code": "5900",
        "name": "Miscellaneous",
        "account_type": "expense",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE CATEGORY MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

EXPENSE_CATEGORY_ACCOUNT_MAP: dict[str, str] = {
    """Maps expense categories to their corresponding account codes.

    When an expense is recorded with a specific category, this mapping determines
    which expense account in the Chart of Accounts should be debited.

    Example:
        If expense category is "rent", account code "5100" (Rent) is debited.
        If expense category is "utilities", account code "5200" (Utilities) is debited.
    """
    "rent": "5100",
    "utilities": "5200",
    "salaries": "5300",
    "supplies": "5400",
    "transport": "5500",
    "marketing": "5600",
    "bank_charges": "5700",
    "phone_internet": "5800",
    "maintenance": "5900",
    "insurance": "5900",
    "taxes": "5900",
    "other": "5900",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  SEED FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════


async def seed_chart_of_accounts(
    tenant_id: UUID,
    session: AsyncSession,
) -> list[dict[str, str]]:
    """Seed the default Chart of Accounts for a new business tenant.

    This function creates all predefined accounts for a tenant. It skips any
    accounts that already exist (identified by tenant_id + code uniqueness).
    This makes the function idempotent -- safe to call multiple times.

    Args:
        tenant_id: The UUID of the business tenant to seed accounts for.
        session: The async SQLAlchemy database session to use for persistence.

    Returns:
        A list of dictionaries representing the accounts that were created.
        Each dictionary contains "code", "name", and "account_type" keys.
        Returns an empty list if all accounts already exist.

    Example:
        >>> created = await seed_chart_of_accounts(tenant_id=uuid4(), session=session)
        >>> print(f"Created {len(created)} accounts")
        Created 22 accounts
    """
    created_accounts: list[dict[str, str]] = []

    for account_def in DEFAULT_ACCOUNTS:
        # Check if account already exists for this tenant (idempotent check)
        existing = await get_account_by_code(
            session=session,
            tenant_id=tenant_id,
            code=account_def["code"],
        )
        if existing:
            continue

        # Create the account using the service layer planning function
        command = ChartOfAccountCreateCommand(
            tenant_id=tenant_id,
            code=account_def["code"],
            name=account_def["name"],
            account_type=account_def["account_type"],
        )

        # Import here to avoid circular imports at module level
        from .service import plan_create_account

        result, account_model = plan_create_account(command)
        await create_account(session=session, account=account_model)
        created_accounts.append(account_def)

    return created_accounts
