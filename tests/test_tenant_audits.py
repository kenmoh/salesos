"""Curated tenant audits: the writes behind the Activity Logs screen.

A money-moving action must land in ``auth_audit_logs`` with details the
mobile tile can render, not vanish into the ledger only.
"""

import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.dependencies import (
    TenantContext,
    get_db,
    get_tenant_context,
    get_tenant_db_context,
)
from app.identity.models import AuthAuditLog
from tests.test_api_routes import _make_app, _make_session, _make_token_data


@pytest.fixture
def tenant_session():
    app = _make_app()
    ctx, session = TenantContext(
        user=_make_token_data(), session=_make_session()
    ), None
    session = ctx.session

    async def override_tenant():
        yield ctx

    async def override_db():
        yield session

    app.dependency_overrides[get_tenant_db_context] = override_tenant
    app.dependency_overrides[get_tenant_context] = override_tenant
    app.dependency_overrides[get_db] = override_db
    return app, ctx, session


def _audit_rows(session) -> list[AuthAuditLog]:
    return [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], AuthAuditLog)
    ]


class TestAccountingAudits:
    async def test_posting_a_journal_writes_an_audit_row(self, tenant_session):
        app, _, session = tenant_session

        from app.accounting import routes as accounting_routes

        accounting_routes.rpc.post_journal = AsyncMock(
            return_value=str(uuid.uuid4())
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/accounting/journals",
                json={
                    "description": "test",
                    "entries": [
                        {
                            "account_id": "00000000-0000-0000-0000-000000000001",
                            "account_code": "100",
                            "debit": 100,
                            "credit": 0,
                        },
                        {
                            "account_id": "00000000-0000-0000-0000-000000000002",
                            "account_code": "200",
                            "debit": 0,
                            "credit": 100,
                        },
                    ],
                    "store_id": str(uuid.uuid4()),
                },
            )

        assert resp.status_code == 201
        rows = _audit_rows(session)
        assert rows, "posting a journal must write an audit row"
        assert rows[-1].action == "journal_created"
        assert json.loads(rows[-1].details)["total"] == 100.0

    async def test_recording_an_ar_payment_writes_an_audit_row(self, tenant_session):
        app, _, session = tenant_session

        from app.accounting import routes as accounting_routes

        accounting_routes.rpc.record_ar_payment = AsyncMock(
            return_value={"invoice_number": "INV-7", "store_id": None}
        )
        accounting_routes._post_payment_journal = AsyncMock()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/accounting/receivable/ar-1/payment",
                json={"amount": 100, "payment_date": "2026-10-22"},
            )

        assert resp.status_code == 200
        rows = _audit_rows(session)
        assert rows, "recording an AR payment must write an audit row"
        assert rows[-1].action == "ar_payment_received"
        assert json.loads(rows[-1].details)["invoice_number"] == "INV-7"
