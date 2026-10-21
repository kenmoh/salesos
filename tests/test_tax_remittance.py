"""Remittance tracking answers two questions with the ledger as the referee.

* How much tax remains un-remitted? Sales credited the tax account; every
  remittance journal debits it back, so ``still_owed`` reads the credit that
  is still standing — never a number the books disagree with.
* Which remittances already posted? They are tagged ``tax_remittance``, so
  the history is every journal bearing that tag and their debit leg.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.accounting import rpc
from app.accounting.models import ChartOfAccount, Journal, JournalEntry
from app.common.db.session import set_rls_context
from app.core.config import settings
from app.core.dependencies import TenantContext, TokenData, get_tenant_db_context
from app.taxes.models import Tax
from tests.test_api_routes import ALL_PERMISSIONS

NOW = datetime.now(UTC)


def _journal_number() -> str:
    return f"JRN-T-{uuid4().hex[:12].upper()}"


async def _seed(chart, tenant, jid, reference_type, entries, store_id=None):
    await chart.execute(
        insert(Journal).values(
            id=jid,
            tenant_id=tenant,
            journal_number=_journal_number(),
            description=reference_type,
            reference_type=reference_type,
            status="posted",
            posted_at=NOW,
            store_id=store_id,
        )
    )
    await chart.execute(
        insert(JournalEntry),
        [
            {
                "id": uuid4(),
                "journal_id": jid,
                "tenant_id": tenant,
                "account_id": account_id,
                "account_code": code,
                "debit": debit,
                "credit": credit,
                "type": type_,
                "status": "posted",
                "posted_at": NOW,
                "amount": amount,
            }
            for account_id, code, debit, credit, type_, amount in entries
        ],
    )


class TestRemittanceTracking:
    @pytest.fixture
    async def engine(self) -> AsyncEngine:
        engine = create_async_engine(
            settings.admin_database_url or settings.database_url
        )
        yield engine
        await engine.dispose()

    async def test_collected_remitted_and_still_owed_come_from_the_ledger(
        self, engine: AsyncEngine
    ):
        from app.accounting.repository import get_tax_remittances, get_taxes_owed

        tenant = uuid4()
        tax_id = uuid4()
        jid = uuid4()
        vat_account_id = uuid4()
        cash_account_id = uuid4()
        try:
            async with engine.begin() as chart:
                await chart.execute(
                    insert(ChartOfAccount).values(
                        id=vat_account_id,
                        tenant_id=tenant,
                        code="2300",
                        name="VAT Payable",
                        account_type="liability",
                        status="active",
                    )
                )
                await chart.execute(
                    insert(ChartOfAccount).values(
                        id=cash_account_id,
                        tenant_id=tenant,
                        code="1000",
                        name="Cash",
                        account_type="asset",
                        status="active",
                    )
                )
                await chart.execute(
                    insert(Tax).values(
                        id=tax_id,
                        tenant_id=tenant,
                        name="VAT",
                        rate=7.5,
                        account_code="2300",
                        is_active=True,
                    )
                )
                # A sale collected 1,000 of VAT (credit) covering its debit leg.
                await _seed(
                    chart,
                    tenant,
                    jid,
                    "sale",
                    [(vat_account_id, "2300", 0, 1000, "liability", -1000)],
                )

            make_session = async_sessionmaker(engine, expire_on_commit=False)

            async with make_session() as session:
                await set_rls_context(
                    session, str(uuid4()), str(tenant), "owner"
                )
                lines = await get_taxes_owed(session, tenant)
                va_line = next(l for l in lines if l["account_code"] == "2300")
                assert va_line["collected"] == 1000
                assert va_line["remitted"] == 0
                assert va_line["still_owed"] == 1000
                assert va_line["tax_names"] == ["VAT"]

            # Remit 400 of it through the same tag the route posts: remitted,
            # and never "owed" again.
            async with engine.begin() as chart:
                remit_id = uuid4()
                await _seed(
                    chart,
                    tenant,
                    remit_id,
                    "tax_remittance",
                    [
                        (vat_account_id, "2300", 400, 0, "liability", 400),
                        (cash_account_id, "1000", 0, 400, "asset", -400),
                    ],
                )

            async with make_session() as session:
                await set_rls_context(
                    session, str(uuid4()), str(tenant), "owner"
                )
                lines = await get_taxes_owed(session, tenant)
                va_line = next(l for l in lines if l["account_code"] == "2300")
                assert va_line["collected"] == 1000
                assert va_line["remitted"] == 400
                assert va_line["still_owed"] == 600

                result = await get_tax_remittances(session, tenant, page=1, page_size=10)
                assert result["total"] == 1
                only = result["remittances"][0]
                assert only["account_code"] == "2300"
                assert only["amount"] == 400
                assert only["journal_number"].startswith("JRN-T-")
        finally:
            async with engine.begin() as chart:
                await chart.execute(
                    delete(JournalEntry).where(JournalEntry.tenant_id == tenant)
                )
                await chart.execute(
                    delete(Journal).where(Journal.tenant_id == tenant)
                )
                await chart.execute(
                    delete(Tax).where(Tax.tenant_id == tenant)
                )
                await chart.execute(
                    delete(ChartOfAccount).where(ChartOfAccount.tenant_id == tenant)
                )

    async def test_store_filter_scopes_the_sums_and_the_history(
        self, engine: AsyncEngine
    ):
        from app.accounting.repository import get_tax_remittances, get_taxes_owed
        from app.stores.models import Store

        tenant = uuid4()
        tax_id = uuid4()
        sale_jid = uuid4()
        remit_jid = uuid4()
        vat_account_id = uuid4()
        cash_account_id = uuid4()
        store_a, store_b = uuid4(), uuid4()
        try:
            async with engine.begin() as chart:
                await chart.execute(
                    insert(ChartOfAccount).values(
                        id=vat_account_id,
                        tenant_id=tenant,
                        code="2300",
                        name="VAT Payable",
                        account_type="liability",
                        status="active",
                    )
                )
                await chart.execute(
                    insert(ChartOfAccount).values(
                        id=cash_account_id,
                        tenant_id=tenant,
                        code="1000",
                        name="Cash",
                        account_type="asset",
                        status="active",
                    )
                )
                await chart.execute(
                    insert(Tax).values(
                        id=tax_id,
                        tenant_id=tenant,
                        name="VAT",
                        rate=7.5,
                        account_code="2300",
                        is_active=True,
                    )
                )
                # Stores referenced only through journal tags; the switcher
                # filter joins live on journals.store_id, not this table.
                await chart.execute(
                    insert(Store).values(
                        id=store_a,
                        tenant_id=tenant,
                        name="Store A",
                    )
                )
                await chart.execute(
                    insert(Store).values(
                        id=store_b,
                        tenant_id=tenant,
                        name="Store B",
                    )
                )
                # Store A collected 1,000; store B remitted 400 of it.
                await _seed(
                    chart,
                    tenant,
                    sale_jid,
                    "sale",
                    [(vat_account_id, "2300", 0, 1000, "liability", -1000)],
                    store_id=store_a,
                )
                await _seed(
                    chart,
                    tenant,
                    remit_jid,
                    "tax_remittance",
                    [
                        (vat_account_id, "2300", 400, 0, "liability", 400),
                        (cash_account_id, "1000", 0, 400, "asset", -400),
                    ],
                    store_id=store_b,
                )

            make_session = async_sessionmaker(engine, expire_on_commit=False)

            async with make_session() as session:
                await set_rls_context(
                    session, str(uuid4()), str(tenant), "owner"
                )

                all_lines = await get_taxes_owed(session, tenant)
                va = next(l for l in all_lines if l["account_code"] == "2300")
                assert va["collected"] == 1000
                assert va["remitted"] == 400
                assert va["still_owed"] == 600

                lines_a = await get_taxes_owed(session, tenant, store_id=store_a)
                va_a = next(l for l in lines_a if l["account_code"] == "2300")
                assert va_a["collected"] == 1000
                assert va_a["remitted"] == 0
                assert va_a["still_owed"] == 1000

                lines_b = await get_taxes_owed(session, tenant, store_id=store_b)
                va_b = next(l for l in lines_b if l["account_code"] == "2300")
                assert va_b["collected"] == 0
                assert va_b["remitted"] == 400
                assert va_b["still_owed"] == -400

                history_b = await get_tax_remittances(
                    session, tenant, page=1, page_size=10, store_id=store_b
                )
                assert history_b["total"] == 1
                history_a = await get_tax_remittances(
                    session, tenant, page=1, page_size=10, store_id=store_a
                )
                assert history_a["total"] == 0
        finally:
            async with engine.begin() as chart:
                await chart.execute(
                    delete(JournalEntry).where(JournalEntry.tenant_id == tenant)
                )
                await chart.execute(
                    delete(Journal).where(Journal.tenant_id == tenant)
                )
                await chart.execute(
                    delete(Store).where(Store.tenant_id == tenant)
                )
                await chart.execute(
                    delete(Tax).where(Tax.tenant_id == tenant)
                )
                await chart.execute(
                    delete(ChartOfAccount).where(ChartOfAccount.tenant_id == tenant)
                )


class TestRemitTaxRoute:
    """The remittance endpoint guards its ceiling against the ledger.

    A merchant cannot remit more tax than the account still carries, and an
    account no tax maps to is not remittable at all: both leave no journal
    and no audit row behind. The repository answers the ledger questions,
    so the route only has to mediate.
    """

    @pytest.fixture
    async def client(self):
        from httpx import ASGITransport, AsyncClient

        from app.core.dependencies import get_tenant_context
        from tests.test_api_routes import _make_app, _make_session, _make_token_data

        app = _make_app()
        self._app = app
        session = _make_session()
        self._session = session
        ctx = TenantContext(user=_make_token_data(), session=session)

        async def override_tenant():
            yield ctx

        app.dependency_overrides[get_tenant_context] = override_tenant
        app.dependency_overrides[get_tenant_db_context] = override_tenant
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


    def _patched_responders(self, *, lines, accounts, balance):
        """Replace the ledger answers the route consults.

        get_taxes_owed and _get_account_balance are imported inside the
        route, so patching their repository homes is what the route sees;
        get_account_by_code is a module import in routes itself.
        rpc.post_journal stands in for the SQL posting; the audit row still
        lands on the session.
        """
        return [
            patch(
                "app.accounting.repository.get_taxes_owed",
                AsyncMock(return_value=lines),
            ),
            patch(
                "app.accounting.routes.get_account_by_code",
                AsyncMock(side_effect=list(accounts)),
            ),
            patch(
                "app.accounting.repository._get_account_balance",
                AsyncMock(return_value=balance),
            ),
            patch.object(rpc, "post_journal", AsyncMock(return_value=str(uuid4()))),
        ]

    def _taxes_owed(self, still_owed):
        return [
            {
                "account_code": "2300",
                "account_name": "VAT Payable",
                "collected": 1000.0,
                "remitted": 1000.0 - still_owed,
                "still_owed": still_owed,
                "tax_names": ["VAT"],
            }
        ]

    def _vat(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=uuid4(), code="2300", name="VAT Payable", account_type="liability"
        )

    def _cash(self, code="1000", name="Cash"):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=uuid4(), code=code, name=name, account_type="asset"
        )

    async def test_method_must_be_cash_or_bank(self, client):
        vat = self._vat()
        lines = self._taxes_owed(1000)
        responders = self._patched_responders(
            lines=lines, accounts=[vat, vat], balance=600.0
        )

        with responders[0], responders[1], responders[2], responders[3]:
            resp = await client.post(
                "/accounting/taxes-remit",
                json={
                    "account_code": "2300",
                    "amount": 100,
                    "method": "card",
                    "payment_date": "2026-10-21",
                },
            )

        assert resp.status_code == 400
        assert "cash or bank" in resp.json()["detail"]

    async def test_remitting_more_than_is_still_owed_is_refused(self, client):
        """900 of the 1,000 collected is the ceiling left; asking 500 of it
        must stop before anything posts."""
        session = self._session
        vat = self._vat()
        lines = self._taxes_owed(100)
        responders = self._patched_responders(
            lines=lines, accounts=[vat, vat], balance=600.0
        )

        with responders[0], responders[1], responders[2], responders[3]:
            resp = await client.post(
                "/accounting/taxes-remit",
                json={
                    "account_code": "2300",
                    "amount": 500,
                    "method": "cash",
                    "payment_date": "2026-10-21",
                },
            )

        assert resp.status_code == 400
        assert "still owed" in resp.json()["detail"]
        session.add.assert_not_called()

    async def test_remitting_an_account_no_tax_maps_to_is_refused(self, client):
        session = self._session
        responders = self._patched_responders(lines=[], accounts=[], balance=0)

        with responders[0], responders[1], responders[2], responders[3]:
            resp = await client.post(
                "/accounting/taxes-remit",
                json={
                    "account_code": "5100",
                    "amount": 5,
                    "method": "cash",
                    "payment_date": "2026-10-21",
                },
            )

        assert resp.status_code == 400
        assert "No tax is owed on account 5100" in resp.json()["detail"]
        session.add.assert_not_called()

    async def test_remitting_within_owed_posts_and_says_what_is_left(self, client):
        vat, cash = self._vat(), self._cash()
        session = self._session
        session.execute.return_value = MagicMock(
            scalar_one=MagicMock(return_value="JRN-990")
        )
        post = None
        responders = self._patched_responders(
            lines=self._taxes_owed(600), accounts=[cash, vat], balance=100.0
        )

        with responders[0], responders[1], responders[2], responders[3] as post_write:
            post = post_write
            resp = await client.post(
                "/accounting/taxes-remit",
                json={
                    "account_code": "2300",
                    "amount": 500,
                    "method": "cash",
                    "notes": "VAT transfer",
                    "payment_date": "2026-10-21",
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["account_code"] == "2300"
        assert body["amount"] == 500
        assert body["still_owed"] == 100.0
        assert body["journal_number"] == "JRN-990"

        entries = post.call_args.kwargs["entries"]
        assert entries[0]["account_code"] == "2300"
        assert entries[0]["debit"] == 500
        assert entries[1]["account_code"] == "1000"
        assert entries[1]["credit"] == 500
        assert post.call_args.kwargs["ref_type"] == "tax_remittance"

        audit = session.add.call_args.args[0]
        assert audit.action == "tax_remittance"

    async def test_bank_method_falls_back_to_cash_when_bank_missing(self, client):
        vat, cash = self._vat(), self._cash()
        session = self._session
        session.execute.return_value = MagicMock(
            scalar_one=MagicMock(return_value="JRN-991")
        )
        responders = self._patched_responders(
            lines=self._taxes_owed(600), accounts=[None, cash, vat], balance=100.0
        )

        with responders[0], responders[1], responders[2], responders[3]:
            resp = await client.post(
                "/accounting/taxes-remit",
                json={
                    "account_code": "2300",
                    "amount": 500,
                    "method": "bank",
                    "payment_date": "2026-10-21",
                },
            )
            assert resp.status_code == 200, resp.text
            entries = rpc.post_journal.call_args.kwargs["entries"]
            assert entries[1]["account_code"] == "1000"



