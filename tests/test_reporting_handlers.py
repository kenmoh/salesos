from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.common.events.envelope import EventEnvelope
from app.reporting.models import DailySalesSummary, PaymentMethodSummary
from app.worker.handlers.reporting import (
    handle_payment_succeeded,
    handle_sale_receipt_created,
    handle_sale_voided,
)


@pytest.mark.parametrize(
    ("handler", "payload", "repository_function", "model_type"),
    [
        (
            handle_sale_receipt_created,
            {"total": 120},
            "upsert_daily_summary",
            DailySalesSummary,
        ),
        (
            handle_sale_voided,
            {"total": 120},
            "upsert_daily_summary",
            DailySalesSummary,
        ),
        (
            handle_payment_succeeded,
            {"amount": 120, "method": "cash"},
            "upsert_payment_method_summary",
            PaymentMethodSummary,
        ),
    ],
)
async def test_reporting_handlers_upsert_planned_model(
    monkeypatch, handler, payload, repository_function, model_type
):
    from app.reporting import repository

    upsert = AsyncMock()
    monkeypatch.setattr(repository, repository_function, upsert)
    tenant_id = uuid4()
    envelope = EventEnvelope(
        event_type="test",
        tenant_id=tenant_id,
        payload=payload,
    )

    await handler(envelope, AsyncMock())

    summary = upsert.await_args.args[1]
    assert isinstance(summary, model_type)
    assert summary.tenant_id == str(tenant_id)
