"""Accounting invariants.

These are the rules a set of books must never break. They failed in this
codebase before (receivables and payables were recorded without a journal, so
their general-ledger control accounts drifted, and equity was computed as a
plug that made the balance check meaningless), so each one is pinned here.

The tests run against the real Postgres in a transaction that is always rolled
back, because the invariants are about SQL behaviour across tables and
functions — mocking them away would test nothing.
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from dotenv import load_dotenv
import asyncpg

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL for invariant tests"
)


class Ledger:
    """A connection pinned to one tenant, inside a rolled-back transaction.

    asyncpg's Connection uses __slots__, so the tenant travels alongside it.
    """

    def __init__(self, conn, tenant_id):
        self.conn = conn
        self.tenant_id = tenant_id

    def __getattr__(self, name):
        return getattr(self.conn, name)


@pytest.fixture
async def db():
    conn = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    tenant = await conn.fetchval("SELECT id FROM tenants LIMIT 1")
    if tenant is None:
        await conn.close()
        pytest.skip("no tenant seeded")

    await conn.execute("BEGIN")
    # RLS is FORCEd on the tenant tables: without these the fixtures see nothing.
    for key, value in (
        ("app.business_id", str(tenant)),
        ("app.user_id", str(tenant)),
        ("app.role", "admin"),
    ):
        await conn.execute(f"SELECT set_config('{key}', $1, true)", value)

    try:
        yield Ledger(conn, tenant)
    finally:
        await conn.execute("ROLLBACK")
        await conn.close()


async def account_balance(conn, code: str, account_type: str) -> Decimal:
    """Natural-sign balance of a single account from posted journal lines."""
    total = await conn.fetchval(
        """
        SELECT COALESCE(SUM(e.debit), 0) - COALESCE(SUM(e.credit), 0)
        FROM journal_entries e
        JOIN chart_of_accounts a ON a.id = e.account_id
        WHERE e.tenant_id = $1
          AND e.status = 'posted'
          AND a.code = $2
        """,
        conn.tenant_id,
        code,
    )
    net = Decimal(total or 0)
    return net if account_type in ("asset", "expense") else -net


async def type_total(conn, account_type: str) -> Decimal:
    """Natural-sign total for a whole account type."""
    total = await conn.fetchval(
        """
        SELECT COALESCE(SUM(e.debit), 0) - COALESCE(SUM(e.credit), 0)
        FROM journal_entries e
        JOIN chart_of_accounts a ON a.id = e.account_id
        WHERE e.tenant_id = $1
          AND e.status = 'posted'
          AND a.account_type = $2
        """,
        conn.tenant_id,
        account_type,
    )
    net = Decimal(total or 0)
    return net if account_type in ("asset", "expense") else -net


async def assert_books_balance(conn) -> Decimal:
    assets = await type_total(conn, "asset")
    liabilities = await type_total(conn, "liability")
    capital = await type_total(conn, "equity")
    revenue = await type_total(conn, "revenue")
    expenses = await type_total(conn, "expense")

    equity = capital + revenue - expenses
    difference = assets - liabilities - equity
    assert difference == 0, (
        f"balance sheet does not balance by {difference}: "
        f"A={assets} L={liabilities} E={equity} "
        f"(capital {capital}, revenue {revenue}, expenses {expenses})"
    )
    return difference


class TestBookkeepingInvariants:
    async def test_every_journal_balances(self, db):
        unbalanced = await db.fetch(
            """
            SELECT j.journal_number, j.description,
                   SUM(e.debit) AS dr, SUM(e.credit) AS cr
            FROM journals j
            JOIN journal_entries e ON e.journal_id = j.id
            WHERE j.tenant_id = $1
            GROUP BY j.id
            HAVING ABS(COALESCE(SUM(e.debit), 0) - COALESCE(SUM(e.credit), 0)) > 0.01
            """,
            db.tenant_id,
        )
        assert not unbalanced, [
            f"{r['journal_number']}: Dr {r['dr']} vs Cr {r['cr']}" for r in unbalanced
        ]

    async def test_no_entry_points_at_a_missing_account(self, db):
        """A uuid that is not in the chart of accounts means a silent void."""
        orphans = await db.fetch(
            """
            SELECT e.account_code, e.description
            FROM journal_entries e
            WHERE e.tenant_id = $1
              AND e.status = 'posted'
              AND NOT EXISTS (
                  SELECT 1 FROM chart_of_accounts a
                  WHERE a.id = e.account_id AND a.tenant_id = e.tenant_id
              )
            LIMIT 5
            """,
            db.tenant_id,
        )
        assert not orphans, [
            f"{r['account_code']} ({r['description']}) has no chart-of-accounts row"
            for r in orphans
        ]

    async def test_balance_sheet_identity_holds(self, db):
        """Assets = Liabilities + (capital + revenue - expenses).

        This only holds when every journal is balanced, which is why it is the
        control that would have caught the old equity plug.
        """
        await assert_books_balance(db)


class TestReceivableSubledgerReconciliation:
    """AR rows and account 1100 must agree, to the naira."""

    async def test_receivables_have_an_opening_journal(self, db):
        missing = await db.fetch(
            """
            SELECT ar.invoice_number, ar.amount
            FROM accounts_receivable ar
            WHERE ar.tenant_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM journals j
                  WHERE j.tenant_id = ar.tenant_id
                    AND j.reference_type = 'receivable'
                    AND j.reference_id = ar.id
              )
            """,
            db.tenant_id,
        )
        assert not missing, [
            f"receivable {r['invoice_number']} ({r['amount']}) has no Dr 1100 journal"
            for r in missing
        ]

    async def test_control_account_matches_subledger(self, db):
        subledger = Decimal(
            await db.fetchval(
                "SELECT COALESCE(SUM(balance), 0) FROM accounts_receivable "
                "WHERE tenant_id = $1",
                db.tenant_id,
            )
            or 0
        )
        control = await account_balance(db, "1100", "asset")
        assert subledger == control, (
            f"receivable sub-ledger {subledger} != GL 1100 {control} "
            f"(difference {subledger - control})"
        )


class TestPayableSubledgerReconciliation:
    async def test_payables_have_an_opening_journal(self, db):
        missing = await db.fetch(
            """
            SELECT ap.bill_number, ap.amount
            FROM accounts_payable ap
            WHERE ap.tenant_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM journals j
                  WHERE j.tenant_id = ap.tenant_id
                    AND j.reference_type = 'payable'
                    AND j.reference_id = ap.id
              )
            """,
            db.tenant_id,
        )
        assert not missing, [
            f"payable {r['bill_number']} ({r['amount']}) has no Cr 2000 journal"
            for r in missing
        ]

    async def test_control_account_matches_subledger(self, db):
        subledger = Decimal(
            await db.fetchval(
                "SELECT COALESCE(SUM(balance), 0) FROM accounts_payable "
                "WHERE tenant_id = $1",
                db.tenant_id,
            )
            or 0
        )
        control = await account_balance(db, "2000", "liability")
        assert subledger == control, (
            f"payable sub-ledger {subledger} != GL 2000 {control} "
            f"(difference {subledger - control})"
        )

    async def test_liability_and_asset_accounts_are_not_negative(self, db):
        """1100 and 2000 are assets and liabilities: both are positive when
        money is owed. A negative balance means a payment hit an account that
        was never booked."""
        for code, account_type in (("1100", "asset"), ("2000", "liability")):
            balance = await account_balance(db, code, account_type)
            assert balance >= 0, f"GL {code} has a negative balance: {balance}"


class TestBalanceSheetFunction:
    """Exercises the real get_balance_sheet, not just the underlying SQL.

    The route's numbers are what users read, so they get their own test: an
    earlier version of this function accumulated every account's balance into
    the expense bucket because it reused a loop variable, which left assets,
    liabilities and revenue at zero while still "balancing".

    Everything happens inside one transaction on one session. Reading the
    sheet on a separate connection from the ledger made the comparison race
    against any write landing in between -- on a live database that fails
    intermittently for no reason.
    """

    async def _compare(self):
        from sqlalchemy import text as sql
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.accounting.repository import get_balance_sheet
        from app.common.db.session import set_rls_context
        from app.core.config import settings

        engine = create_async_engine(settings.database_url)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                transaction = await session.begin()
                try:
                    tenant = (
                        await session.execute(sql("SELECT id FROM tenants LIMIT 1"))
                    ).scalar()
                    tenant_id = str(tenant)
                    await set_rls_context(session, tenant_id, tenant_id, "admin")

                    async def ledger(account_type: str) -> Decimal:
                        net = Decimal(
                            await session.scalar(
                                sql(
                                    """
                                    SELECT COALESCE(SUM(e.debit), 0)
                                         - COALESCE(SUM(e.credit), 0)
                                    FROM journal_entries e
                                    JOIN chart_of_accounts a ON a.id = e.account_id
                                    WHERE e.tenant_id = :t
                                      AND e.status = 'posted'
                                      AND a.account_type = :kind
                                    """
                                ),
                                {"t": tenant_id, "kind": account_type},
                            )
                            or 0
                        )
                        # Assets and expenses read debit-positive; the others
                        # are credit-positive.
                        return net if account_type in ("asset", "expense") else -net

                    sheet = await get_balance_sheet(session, tenant)

                    return sheet, {
                        "asset": await ledger("asset"),
                        "liability": await ledger("liability"),
                        "equity": await ledger("equity"),
                        "revenue": await ledger("revenue"),
                        "expense": await ledger("expense"),
                    }
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    async def test_sections_match_the_ledger(self):
        sheet, ledger = await self._compare()

        assert sheet["assets"] == ledger["asset"], (
            f"reported assets {sheet['assets']} != ledger {ledger['asset']}"
        )
        assert sheet["liabilities"] == ledger["liability"]
        assert sheet["capital"] == ledger["equity"]
        assert sheet["revenue"] == ledger["revenue"]
        assert sheet["expenses"] == ledger["expense"]

    async def test_equity_is_capital_plus_earnings(self):
        sheet, _ = await self._compare()
        assert sheet["equity"] == sheet["capital"] + sheet["current_earnings"]

    async def test_balance_check_is_zero(self):
        sheet, _ = await self._compare()
        assert sheet["balance_check"] == 0, (
            f"balance check is {sheet['balance_check']}"
        )

    async def test_reconciliations_are_reported(self):
        sheet, _ = await self._compare()
        assert sheet["receivable_difference"] == 0
        assert sheet["payable_difference"] == 0


class TestPaymentBooking:
    async def test_every_payment_has_a_journal(self, db):
        """Each AR/AP payment must be matched by a cash journal, otherwise the
        balance moves without the ledger."""
        unbooked_ar = await db.fetchval(
            """
            SELECT COUNT(*) FROM ar_payments p
            WHERE p.tenant_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM journals j
                  WHERE j.tenant_id = p.tenant_id
                    AND j.reference_type = 'ar_payment'
                    AND j.reference_id = p.ar_id
              )
            """,
            db.tenant_id,
        )
        assert not unbooked_ar, (
            f"{unbooked_ar} receivable payments have no cash journal"
        )

    async def test_payment_ledger_totals_match_amount_paid(self, db):
        mismatch = await db.fetch(
            """
            SELECT ar.invoice_number, ar.amount_paid, COALESCE(SUM(p.amount), 0) AS paid
            FROM accounts_receivable ar
            LEFT JOIN ar_payments p ON p.ar_id = ar.id AND p.tenant_id = ar.tenant_id
            WHERE ar.tenant_id = $1
            GROUP BY ar.id, ar.invoice_number, ar.amount_paid
            HAVING ar.amount_paid <> COALESCE(SUM(p.amount), 0)
            """,
            db.tenant_id,
        )
        assert not mismatch, [
            f"{r['invoice_number']}: amount_paid {r['amount_paid']} "
            f"vs ledger {r['paid']}"
            for r in mismatch
        ]