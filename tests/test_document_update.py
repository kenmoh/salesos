"""Draft documents can be edited; finalized documents cannot.

Covers the pure planning layer (no database): a draft document accepts header
and line-item changes with totals recomputed, while any non-draft document is
refused.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.documents.models import Document
from app.documents.schemas import DocumentItemLine, DocumentUpdateCommand
from app.documents.service import plan_document_update


def _doc(**overrides) -> Document:
    payload = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        doc_number="INV-20261101-ABCDEF01",
        doc_type="invoice",
        status="draft",
        customer_id=None,
        customer_name=None,
        customer_email=None,
        customer_phone=None,
        customer_address=None,
        subtotal=0.0,
        discount=0.0,
        tax=0.0,
        total=0.0,
        due_date=None,
        notes=None,
        terms=None,
        linked_sale_id=None,
        store_id=None,
        show_bank_details=False,
        created_by=uuid4(),
    )
    payload.update(overrides)
    return Document(**payload)


def _command(**overrides) -> DocumentUpdateCommand:
    payload = dict(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        customer_name="Ada",
        customer_email="ada@example.com",
        items=[
            DocumentItemLine(description="Widget", qty=Decimal("1"), unit_price=Decimal("1000"))
        ],
    )
    payload.update(overrides)
    return DocumentUpdateCommand(**payload)


class TestUpdateDraftDocument:
    def test_customer_fields_are_updated(self):
        doc = _doc()
        result, updated, items, _events = plan_document_update(
            _command(items=None, customer_name="Ada Lovelace", customer_email="ada@example.com"),
            doc,
        )

        assert updated.customer_name == "Ada Lovelace"
        assert updated.customer_email == "ada@example.com"
        assert result.doc_number == "INV-20261101-ABCDEF01"
        assert result.status == "draft"
        assert items == []
        assert items == []

    def test_bank_toggle_and_optional_fields_are_updated(self):
        due = datetime(2026, 12, 1, tzinfo=None)
        doc = _doc()
        result, updated, _items, _events = plan_document_update(
            _command(
                due_date=due,
                notes="Handle with care",
                terms="Net 30",
                store_id=uuid4(),
                show_bank_details=True,
            ),
            doc,
        )

        assert updated.due_date == due
        assert updated.notes == "Handle with care"
        assert updated.terms == "Net 30"
        assert updated.store_id is not None
        assert updated.show_bank_details is True
        assert result.show_bank_details is True

    def test_items_are_left_untouched_when_not_provided(self):
        doc = _doc(subtotal=500.0, total=500.0)
        result, updated, items, _events = plan_document_update(
            _command(items=None, customer_name="Ada"), doc
        )

        assert items == []
        assert updated.subtotal == 500.0
        assert updated.total == 500.0
        assert result.item_count == 0

    def test_replacing_items_recomputes_totals(self):
        doc = _doc()
        result, updated, items, _events = plan_document_update(
            _command(
                items=[
                    DocumentItemLine(
                        description="Widget", qty=Decimal("2"), unit_price=Decimal("1000")
                    ),
                    DocumentItemLine(
                        description="Gadget", qty=Decimal("1"), unit_price=Decimal("500")
                    ),
                ]
            ),
            doc,
        )

        assert len(items) == 2
        assert updated.subtotal == 2500.0
        assert updated.total == 2500.0
        assert result.subtotal == 2500.0
        assert result.total == 2500.0
        assert result.item_count == 2
        assert items[0].line_total == 2000.0
        assert items[1].line_total == 500.0

    def test_discount_and_tax_flow_through_on_update(self):
        doc = _doc()
        result, updated, items, _events = plan_document_update(
            _command(
                items=[
                    DocumentItemLine(
                        description="Widget",
                        qty=Decimal("1"),
                        unit_price=Decimal("1000"),
                        discount_pct=Decimal("10"),
                        tax_rate=Decimal("7.5"),
                    )
                ]
            ),
            doc,
        )

        assert updated.discount == 100.0
        assert updated.subtotal == 1000.0
        assert abs(updated.tax - 67.5) < 1e-6
        assert abs(updated.total - 967.5) < 1e-6
        assert len(items) == 1


class TestUpdateRequiresDraft:
    def test_non_draft_document_is_refused(self):
        doc = _doc(status="sent")
        with pytest.raises(ValueError, match="document_not_draft"):
            plan_document_update(_command(customer_name="Ada"), doc)

    def test_issued_document_is_refused(self):
        doc = _doc(status="issued")
        with pytest.raises(ValueError, match="document_not_draft"):
            plan_document_update(_command(customer_name="Ada"), doc)

    def test_void_document_is_refused(self):
        doc = _doc(status="void")
        with pytest.raises(ValueError, match="document_not_draft"):
            plan_document_update(_command(customer_name="Ada"), doc)
