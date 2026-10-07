"""Tax used to be six overlapping guesses; it is now one model.

A product carries the taxes that apply to it, the cart and the receipt only
aggregate what those lines produced, and each tax books to the liability
account named on the tax itself. What is pinned here:

* the arithmetic, including two taxes on one line and a cart discount spread
  by gross share;
* a sale and a document charging the same figure for the same lines;
* each tax landing in its own liability account in the journal;
* the two routes a client could previously have priced its own tax.
"""

from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.accounting.service import plan_sale_journal
from app.sales.schemas import SaleCreateCommand, SaleItemLine
from app.sales.service import plan_sale_creation
from app.taxes.resolve import product_tax_map, taxes_for_ids
from app.taxes.calc import (
    DEFAULT_TAX_ACCOUNT,
    VAT_PAYABLE,
    TaxLine,
    compute_taxes,
    default_account_for,
)

TENANT = uuid4()
STORE = uuid4()
CASHIER = uuid4()

VAT = {"id": None, "name": "VAT", "rate": 7.5, "account_code": "2300"}
CONSUMPTION = {"id": None, "name": "Consumption tax", "rate": 5.0, "account_code": "2400"}


class TestTwoTaxesOnOneLine:
    """A good that is both VAT and excise is charged both, not the first one
    that happens to be assigned."""

    def test_both_taxes_charge_and_are_listed_separately(self):
        calc = compute_taxes([TaxLine(qty=1, unit_price=5000, taxes=[VAT, CONSUMPTION])])

        assert calc.subtotal == 5000
        assert calc.line_tax_totals[0] == 625
        assert calc.total_tax == 625
        assert calc.taxable + calc.total_tax == 5625

        names = {t["name"]: t for t in calc.breakdown}
        assert names["VAT"]["amount"] == 375
        assert names["Consumption tax"]["amount"] == 250

    def test_the_rates_and_accounts_stay_attached_to_their_own_tax(self):
        calc = compute_taxes([TaxLine(qty=1, unit_price=5000, taxes=[VAT, CONSUMPTION])])

        accounts = {t["name"]: t["account_code"] for t in calc.line_taxes[0]}
        assert accounts == {"VAT": "2300", "Consumption tax": "2400"}
        # The line's own rate column is the sum, used for display only.
        assert calc.line_rates[0] == 12.5

    def test_the_same_tax_assigned_twice_charges_once(self):
        calc = compute_taxes([TaxLine(qty=1, unit_price=1000, taxes=[VAT, dict(VAT)])])

        assert calc.line_tax_totals[0] == 75
        assert len(calc.breakdown) == 1


class TestDiscountComesOffTheBase:
    """Tax is owed on money the customer actually pays, never on the part a
    discount took back."""

    def test_a_cart_discount_is_spread_by_gross_share(self):
        calc = compute_taxes(
            [
                TaxLine(qty=1, unit_price=4000, taxes=[VAT]),
                TaxLine(qty=1, unit_price=1000, taxes=[VAT]),
            ],
            cart_discount=500,
        )

        # 4000:1000 splits 500 as 400:100, so the bases are 3600 and 900.
        assert calc.line_bases == [3600.0, 900.0]
        assert calc.taxable == 4500
        assert calc.discount == 500
        assert calc.total_tax == 337.5

    def test_a_line_discount_lands_before_tax(self):
        calc = compute_taxes([TaxLine(qty=2, unit_price=500, discount_pct=10, taxes=[VAT])])

        assert calc.line_bases[0] == 900
        assert calc.total_tax == 67.5

    def test_a_line_discounted_to_zero_pays_no_tax(self):
        calc = compute_taxes(
            [TaxLine(qty=1, unit_price=1000, discount_pct=100, taxes=[VAT])]
        )

        assert calc.line_bases[0] == 0
        assert calc.total_tax == 0


class TestAccountComesFromTheTax:
    def test_a_tax_called_vat_books_to_vat_payable(self):
        assert default_account_for("VAT") == VAT_PAYABLE
        assert default_account_for("VAT (7.5%)") == VAT_PAYABLE

    def test_any_other_tax_books_to_its_own_account(self):
        assert default_account_for("Consumption tax") == DEFAULT_TAX_ACCOUNT
        assert default_account_for("Withholding") == DEFAULT_TAX_ACCOUNT

    def test_a_tax_with_no_account_of_its_own_falls_back_to_the_default(self):
        calc = compute_taxes(
            [TaxLine(qty=1, unit_price=100, taxes=[{"name": "Whatevs", "rate": 10}])]
        )

        assert calc.line_taxes[0][0]["account_code"] == DEFAULT_TAX_ACCOUNT


class TestSaleAndDocumentAgree:
    """Converting a document must not change the figure the customer was
    quoted, which means both sides have to run the same arithmetic."""

    def test_the_same_lines_produce_the_same_totals(self):
        from app.documents.schemas import DocumentCreateCommand, DocumentItemLine
        from app.documents.service import plan_document_creation

        taxes = [VAT, CONSUMPTION]
        _, sale, sale_items, _ = plan_sale_creation(
            SaleCreateCommand(
                tenant_id=TENANT,
                cashier_id=CASHIER,
                store_id=STORE,
                items=[
                    SaleItemLine(
                        product_id=uuid4(),
                        product_name="Thing",
                        qty=1,
                        unit_price=5000,
                        taxes=taxes,
                    )
                ],
                discount=Decimal("0"),
            )
        )
        _, doc, doc_items, _ = plan_document_creation(
            DocumentCreateCommand(
                tenant_id=TENANT,
                actor_id=CASHIER,
                doc_type="invoice",
                items=[
                    DocumentItemLine(
                        description="Thing", qty=1, unit_price=5000, taxes=taxes
                    )
                ],
            )
        )

        # One calculator, one answer: converting the document cannot reprice it.
        assert sale_items[0].tax_breakdown == doc_items[0].tax_breakdown
        assert float(doc.tax) == 625
        assert float(doc.total) == 5625
        assert float(doc.total) == float(sale.total)
        assert sale_items[0].tax_rate == doc_items[0].tax_rate
        assert float(sale_items[0].tax_breakdown[0]["amount"]) == 375

    def test_a_document_line_with_a_bare_rate_is_still_taxed(self):
        """A caller that only knows a rate books as a generic tax rather than
        quietly charging nothing."""
        from app.documents.schemas import DocumentCreateCommand, DocumentItemLine
        from app.documents.service import plan_document_creation

        _, doc, doc_items, _ = plan_document_creation(
            DocumentCreateCommand(
                tenant_id=TENANT,
                actor_id=CASHIER,
                doc_type="invoice",
                items=[
                    DocumentItemLine(
                        description="Thing", qty=1, unit_price=1000, tax_rate=Decimal("7.5")
                    )
                ],
            )
        )

        assert doc.tax == 75
        assert doc_items[0].tax_breakdown[0]["account_code"] == DEFAULT_TAX_ACCOUNT


class TestJournalBooksEachTaxWhereItBelongs:
    def _journal(self, tax_lines, tax_amount, sale_items=None):
        return plan_sale_journal(
            tenant_id=TENANT,
            sale_id=uuid4(),
            sale_number="SL-1",
            sale_items=sale_items or [
                {"product_name": "Thing", "qty": 1, "unit_price": 5000, "cost_price": 0}
            ],
            total=5625,
            discount=0,
            tax_amount=tax_amount,
            tax_lines=tax_lines,
            cashier_id=CASHIER,
            payment_method="cash",
            store_id=STORE,
        )

    def test_each_tax_gets_its_own_credit(self):
        journal, entries = self._journal(
            [
                {"name": "VAT", "amount": 375, "account_code": "2300"},
                {"name": "Consumption tax", "amount": 250, "account_code": "2400"},
            ],
            tax_amount=625,
        )

        credits = {e.account_code: e for e in entries if e.credit}
        assert credits["2300"].credit == 375
        assert credits["2400"].credit == 250
        assert "VAT collected" in credits["2300"].description
        assert "Consumption tax collected" in credits["2400"].description

    def test_revenue_is_net_of_tax_so_the_journals_still_balance(self):
        _, entries = self._journal(
            [
                {"name": "VAT", "amount": 375, "account_code": "2300"},
                {"name": "Consumption tax", "amount": 250, "account_code": "2400"},
            ],
            tax_amount=625,
        )

        assert sum(e.debit for e in entries) == sum(e.credit for e in entries)
        revenue = next(e for e in entries if e.account_code == "4000")
        assert revenue.credit == 5000

    def test_two_taxes_owing_to_the_same_account_are_one_credit(self):
        _, entries = self._journal(
            [
                {"name": "Service tax", "amount": 50, "account_code": "2400"},
                {"name": "Excise", "amount": 25, "account_code": "2400"},
            ],
            tax_amount=75,
        )

        credits = [e for e in entries if e.account_code == "2400"]
        assert len(credits) == 1
        assert credits[0].credit == 75
        assert "Service tax + Excise collected" in credits[0].description

    def test_a_sale_that_only_knows_a_total_still_books_vat(self):
        _, entries = self._journal([], tax_amount=75)

        vat = next(e for e in entries if e.account_code == "2300")
        assert vat.credit == 75
        assert "VAT collected" in vat.description

    def test_a_sale_with_no_tax_owes_no_tax(self):
        _, entries = self._journal([], tax_amount=0)

        assert not any(e.account_code in ("2300", DEFAULT_TAX_ACCOUNT) for e in entries)


class TestTheClientCannotPriceItsTax:
    async def test_a_product_cannot_be_created_with_a_tax_we_do_not_own(self):
        from app.common import bridge

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.add = MagicMock()
        session.commit = AsyncMock()
        sdb = MagicMock()
        sdb.return_value.session.return_value = session

        with (
            patch.object(bridge, "_get_sdb", sdb),
            # The tenant has no such tax, so resolution comes back empty.
            patch.object(bridge, "taxes_for_ids", AsyncMock(return_value=[])),
        ):
            with pytest.raises(ValueError, match="unknown_tax_id"):
                await bridge.create_product_for_store(
                    tenant_id=str(TENANT),
                    store_id=str(STORE),
                    name="Thing",
                    selling_price=Decimal("100"),
                    tax_ids=[str(uuid4())],
                )

        # Nothing may be written for a product whose tax we could not verify.
        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_an_unknown_tax_id_resolves_to_nothing_against_the_real_query(self):
        """Tenant isolation and the active-only filter both live in this one
        query: a tax belonging to nobody applies to nothing."""
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import settings

        engine = create_async_engine(
            settings.admin_database_url or settings.database_url
        )
        try:
            async with engine.connect() as session:
                found = await taxes_for_ids(session, TENANT, [uuid4()])
        finally:
            await engine.dispose()

        assert found == []


class TestConversionKeepsTheQuotedTax:
    async def test_the_documents_tax_reaches_the_sale_unchanged(self):
        from app.common import bridge

        breakdown = [
            {
                "id": None,
                "name": "VAT",
                "rate": 7.5,
                "amount": 75,
                "account_code": "2300",
            }
        ]

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.commit = AsyncMock()
        empty = MagicMock()
        empty.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=empty)
        sdb = MagicMock()
        sdb.return_value.session.return_value = session

        doc = NS(
            id=uuid4(),
            tenant_id=str(TENANT),
            doc_type="invoice",
            status="sent",
            discount=0,
            customer_name=None,
            customer_phone=None,
            store_id=STORE,
            linked_sale_id=None,
            doc_number="INV-1",
            total=1075,
        )
        item = NS(
            product_id=None,
            description="Thing",
            qty=1,
            unit_price=1000,
            discount_pct=0,
            tax_breakdown=breakdown,
        )
        create_sale = AsyncMock(
            return_value={"id": str(uuid4()), "sale_number": "SALE-1", "total": 1075}
        )

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch("app.documents.repository.get_document_by_id", AsyncMock(return_value=doc)),
            patch("app.documents.repository.get_document_items", AsyncMock(return_value=[item])),
            patch.object(bridge, "create_sale_via_service", create_sale),
            patch("app.documents.repository.update_document_status", AsyncMock()),
        ):
            await bridge.convert_document_to_sale(
                tenant_id=str(TENANT),
                document_id=str(uuid4()),
                cashier_id=str(uuid4()),
            )

        sent_items = create_sale.call_args.kwargs["items"]
        assert sent_items[0]["taxes"] == breakdown


class TestTheDetailGroupsTheLinesByTax:
    async def test_the_header_breakdown_is_the_lines_summed_by_tax(self):
        from app.common import bridge

        vat_line = {
            "id": None,
            "name": "VAT",
            "rate": 7.5,
            "amount": 375.0,
            "account_code": "2300",
        }
        consumption_line = {
            "id": None,
            "name": "Consumption tax",
            "rate": 5.0,
            "amount": 250.0,
            "account_code": "2400",
        }

        doc = NS(
            id=uuid4(),
            tenant_id=str(TENANT),
            doc_number="INV-9",
            doc_type="invoice",
            status="sent",
            customer_id=None,
            customer_name=None,
            customer_email=None,
            customer_phone=None,
            customer_address=None,
            store_id=None,
            subtotal=5000.0,
            discount=0.0,
            tax=625.0,
            total=5625.0,
            due_date=None,
            notes=None,
            terms=None,
            linked_sale_id=None,
            pdf_url=None,
            created_at=None,
        )
        items = [
            NS(
                id=uuid4(),
                product_id=None,
                description="A",
                qty=1,
                unit_price=5000,
                discount_pct=0,
                tax_rate=None,
                line_total=5000,
                tax_breakdown=[vat_line, consumption_line],
            ),
            NS(
                id=uuid4(),
                product_id=None,
                description="B",
                qty=1,
                unit_price=1250,
                discount_pct=0,
                tax_rate=None,
                line_total=1250,
                tax_breakdown=[{**vat_line, "amount": 93.75}],
            ),
        ]

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        sdb = MagicMock()
        sdb.return_value.session.return_value = session

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch(
                "app.documents.repository.get_document_by_id",
                AsyncMock(return_value=doc),
            ),
            patch(
                "app.documents.repository.get_document_items",
                AsyncMock(return_value=items),
            ),
        ):
            result = await bridge.get_document_by_id(
                tenant_id=str(TENANT), document_id=str(uuid4())
            )

        breakdown = {t["name"]: t for t in result["tax_breakdown"]}
        # The two VAT lines sum into one row; the other tax stays its own row.
        assert breakdown["VAT"]["amount"] == 468.75
        assert breakdown["VAT"]["account_code"] == "2300"
        assert breakdown["Consumption tax"]["amount"] == 250
        assert breakdown["Consumption tax"]["account_code"] == "2400"
        assert len(result["tax_breakdown"]) == 2
        # The lines keep their own snapshots beside them.
        assert result["items"][0]["taxes"] == [vat_line, consumption_line]


class TestResolverReadsTheProductNotTheRequest:
    async def test_a_product_with_no_assignment_is_untaxed(self):
        pid = uuid4()

        class Result:
            def all(self):
                return [(pid, [])]

        session = MagicMock()
        session.execute = AsyncMock(return_value=Result())

        tax_map = await product_tax_map(session, TENANT, [pid])

        # An untaxed product, not a missing one: the cart still knows about it.
        assert tax_map == {str(pid): []}
        # No assignment means no tax query either.
        assert session.execute.await_count == 1
