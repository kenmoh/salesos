"""Documents are charged a flat rate per creation; sales pay a share.

Every document — quote, invoice, receipt or purchase order, draft or not —
books the platform's flat ``document_fee`` at creation, so the charge cannot
depend on the document's total or on what later becomes of it. Converted sales
are merely sales: they are not booked a fee on conversion, and if one is ever
completed through the payments module the normal sale commission applies.

The same gap showed up in the tenant's own books: an unpaid sale produced no
journal, so revenue and the receivable were both absent until payment. Sales are
now booked when they are created, debiting receivables, and the payment settles
them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.accounting.service import plan_sale_journal

TENANT = uuid4()
SALE = uuid4()


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


class TestFeeOnDocuments:
    async def test_a_converted_sale_is_not_charged(self):
        """Conversion turns a document into a sale; the flat fee was already
        taken at creation and the sale, if ever completed by a payment, is
        charged as a sale like any other."""
        from app.common import bridge

        session = _session()
        doc = SimpleNamespace(
            id=uuid4(),
            tenant_id=str(TENANT),
            doc_type="quote",
            status="sent",
            discount=0,
            customer_name=None,
            customer_phone=None,
            store_id=uuid4(),
            linked_sale_id=None,
            total=100.0,
            doc_number="QT-1",
        )

        with (
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.documents.repository.get_document_by_id",
                AsyncMock(return_value=doc),
            ),
            patch(
                "app.documents.repository.get_document_items",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.documents.repository.update_document_status", AsyncMock()
            ),
            patch.object(
                bridge,
                "create_sale_via_service",
                AsyncMock(
                    return_value={
                        "id": str(SALE),
                        "sale_number": "SL-1",
                        "total": 100.0,
                    }
                ),
            ),
            patch(
                "app.platform.fee_calculator.book_document_fee",
                AsyncMock(return_value=None),
            ) as charge,
        ):
            sdb.return_value.session.return_value = session
            await bridge.convert_document_to_sale(
                tenant_id=str(TENANT),
                document_id=str(doc.id),
                cashier_id=str(uuid4()),
            )

        charge.assert_not_awaited()

    async def test_every_document_is_charged_at_creation(self):
        """A standalone receipt is one of the four types, and the flat fee is
        taken when the document exists — not only when it leaves draft."""
        from app.common import bridge

        session = _session()
        doc = SimpleNamespace(
            id=uuid4(),
            tenant_id=TENANT,
            doc_type="receipt",
            status="issued",
            linked_sale_id=None,
            total=500.0,
            doc_number="RCP-1",
        )

        with (
            patch(
                "app.documents.repository.create_document",
                AsyncMock(),
            ),
            patch(
                "app.documents.repository.create_document_items",
                AsyncMock(),
            ),
            patch(
                "app.documents.service.plan_document_creation",
                return_value=(
                    SimpleNamespace(model_dump=lambda mode: {"id": str(doc.id)}),
                    doc,
                    [],
                    [],
                ),
            ),
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.platform.fee_calculator.get_pending_fee_balance",
                AsyncMock(return_value=0.0),
            ),
            patch(
                "app.platform.fee_calculator.get_max_pending_balance",
                AsyncMock(return_value=1000.0),
            ),
            patch(
                "app.platform.fee_calculator.get_document_fee",
                AsyncMock(return_value=200.0),
            ),
            patch(
                "app.platform.fee_calculator.book_document_fee",
                AsyncMock(return_value={"platform_fee": 200.0}),
            ) as charge,
        ):
            sdb.return_value.session.return_value = session
            await bridge.create_document(
                tenant_id=str(TENANT),
                actor_id=str(uuid4()),
                doc_type="receipt",
                items=[{"description": "x", "qty": 1, "unit_price": 500}],
            )

        charge.assert_awaited_once()
        assert charge.await_args.kwargs["document_id"] == doc.id

    async def test_a_document_linked_to_a_sale_is_still_charged(self):
        """A receipt written for an existing sale still costs its flat fee:
        the fee bills the document's creation, while the sale it belongs to
        is charged a share only when that sale completes."""
        from app.common import bridge

        session = _session()
        doc = SimpleNamespace(
            id=uuid4(),
            tenant_id=TENANT,
            doc_type="receipt",
            status="issued",
            linked_sale_id=uuid4(),
            total=500.0,
            doc_number="RCP-2",
        )

        with (
            patch(
                "app.documents.repository.create_document", AsyncMock()
            ),
            patch(
                "app.documents.repository.create_document_items", AsyncMock()
            ),
            patch(
                "app.documents.service.plan_document_creation",
                return_value=(
                    SimpleNamespace(model_dump=lambda mode: {"id": str(doc.id)}),
                    doc,
                    [],
                    [],
                ),
            ),
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.platform.fee_calculator.get_pending_fee_balance",
                AsyncMock(return_value=0.0),
            ),
            patch(
                "app.platform.fee_calculator.get_max_pending_balance",
                AsyncMock(return_value=1000.0),
            ),
            patch(
                "app.platform.fee_calculator.book_document_fee",
                AsyncMock(return_value={"platform_fee": 200.0}),
            ) as charge,
        ):
            sdb.return_value.session.return_value = session
            await bridge.create_document(
                tenant_id=str(TENANT),
                actor_id=str(uuid4()),
                doc_type="receipt",
                items=[{"description": "x", "qty": 1, "unit_price": 500}],
                linked_sale_id=str(doc.linked_sale_id),
            )

        charge.assert_awaited_once()
        assert charge.await_args.kwargs["document_id"] == doc.id


class TestUnpaidSalesAreBooked:
    def test_an_unpaid_sale_debits_the_receivable_not_cash(self):
        journal, entries = plan_sale_journal(
            tenant_id=TENANT,
            sale_id=SALE,
            sale_number="SL-2",
            sale_items=[{"product_name": "x", "qty": 1, "unit_price": 100}],
            total=100.0,
            discount=0.0,
            settled=False,
        )
        codes = {e.account_code: e for e in entries}
        assert "1100" in codes, "an unpaid sale must debit receivables"
        assert codes["1100"].debit == 100.0
        # No money has moved, so cash must not be touched.
        assert "1000" not in codes
        assert "1010" not in codes

    def test_a_paid_sale_still_debits_cash(self):
        _journal, entries = plan_sale_journal(
            tenant_id=TENANT,
            sale_id=SALE,
            sale_number="SL-3",
            sale_items=[{"product_name": "x", "qty": 1, "unit_price": 100}],
            total=100.0,
            discount=0.0,
            settled=True,
        )
        codes = {e.account_code for e in entries}
        assert "1000" in codes
        assert "1100" not in codes

    @pytest.mark.parametrize("total,tax", [(100.0, 0.0), (120.0, 20.0)])
    def test_the_entry_still_balances(self, total, tax):
        _journal, entries = plan_sale_journal(
            tenant_id=TENANT,
            sale_id=SALE,
            sale_number="SL-4",
            sale_items=[{"product_name": "x", "qty": 1, "unit_price": total}],
            total=total,
            discount=0.0,
            tax_amount=tax,
            settled=False,
        )
        assert sum(e.debit for e in entries) == pytest.approx(
            sum(e.credit for e in entries)
        )
        assert sum(e.debit for e in entries) == pytest.approx(total)