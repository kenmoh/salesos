"""Tests for linking documents to customers and customer types.

Covers the pure planning layer (no database): the document create command
carrying a customer, the status-change event reporting the document's
customer instead of its creator, the repository's customer filter, and the
customer type field reaching API responses.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.common.bridge import _customer_to_dict
from app.customers.models import Customer, CustomerType
from app.documents.models import Document
from app.documents.repository import list_documents_by_tenant
from app.documents.schemas import (
    DocumentCreateCommand,
    DocumentItemLine,
    DocumentStatusCommand,
)
from app.documents.service import plan_document_creation, plan_status_change


def _command(**overrides) -> DocumentCreateCommand:
    payload = dict(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        doc_type="invoice",
        items=[
            DocumentItemLine(
                description="Widget", qty=Decimal("1"), unit_price=Decimal("1000")
            )
        ],
    )
    payload.update(overrides)
    return DocumentCreateCommand(**payload)


class TestDocumentCreationLinksCustomer:
    def test_customer_id_is_persisted_on_the_document(self):
        customer_id = uuid4()
        _result, doc, _items, _outbox = plan_document_creation(
            _command(customer_id=customer_id, customer_name="Ada")
        )
        assert doc.customer_id == customer_id

    def test_customer_id_is_optional(self):
        _result, doc, _items, _outbox = plan_document_creation(
            _command(customer_name="Walk-in")
        )
        assert doc.customer_id is None


class TestStatusChangeCarriesCustomer:
    def _doc(self, customer_id, created_by) -> Document:
        return Document(
            id=uuid4(),
            tenant_id=uuid4(),
            doc_number="INV-20260904-ABCDEF01",
            doc_type="invoice",
            status="draft",
            customer_id=customer_id,
            customer_name="Ada",
            created_by=created_by,
        )

    def test_event_reports_the_document_customer_not_its_creator(self):
        customer_id = uuid4()
        created_by = uuid4()
        doc = self._doc(customer_id, created_by)

        command = DocumentStatusCommand(
            document_id=doc.id, tenant_id=doc.tenant_id, actor_id=created_by, new_status="sent"
        )
        _doc, outbox = plan_status_change(command, doc)

        payload = outbox[0].event.payload
        assert payload["customer_id"] == str(customer_id)
        assert payload["customer_id"] != str(created_by)

    def test_event_customer_is_none_when_document_has_none(self):
        created_by = uuid4()
        doc = self._doc(None, created_by)

        command = DocumentStatusCommand(
            document_id=doc.id, tenant_id=doc.tenant_id, actor_id=created_by, new_status="sent"
        )
        _doc, outbox = plan_status_change(command, doc)

        assert outbox[0].event.payload["customer_id"] is None


class TestListDocumentsCustomerFilter:
    @staticmethod
    def _where_clause(session) -> str:
        query = session.execute.await_args_list[0].args[0]
        sql = str(query.compile(compile_kwargs={"literal_binds": True}))
        return sql.split("WHERE", 1)[1]

    async def test_customer_id_adds_a_where_clause(self):
        session = AsyncMock()
        session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        await list_documents_by_tenant(
            session, tenant_id=uuid4(), doc_type="invoice", customer_id=uuid4()
        )

        assert "documents.customer_id" in self._where_clause(session)

    async def test_no_customer_id_leaves_the_filter_off(self):
        session = AsyncMock()
        session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        await list_documents_by_tenant(session, tenant_id=uuid4(), doc_type="invoice")

        assert "customer_id" not in self._where_clause(session)


class TestCustomerTypeField:
    def test_dict_exposes_type(self):
        customer = SimpleNamespace(
            id=uuid4(),
            name="Acme Supplies",
            type=CustomerType.VENDOR,
            phone=None,
            email=None,
            address=None,
            created_at=None,
            updated_at=None,
        )
        assert _customer_to_dict(customer)["type"] == "vendor"

    def test_defaults_to_customer(self):
        column = Customer.__table__.c.type
        assert column.default.arg == CustomerType.CUSTOMER
        assert column.server_default.arg == "customer"

    @pytest.mark.parametrize("bad", ["supplier", "", "CUSTOMER", 1])
    def test_api_rejects_unknown_types(self, bad):
        from pydantic import ValidationError

        from app.customers.routes import CustomerCreate, CustomerUpdate

        with pytest.raises(ValidationError):
            CustomerCreate(name="Ada", type=bad)

        with pytest.raises(ValidationError):
            CustomerUpdate(type=bad)
