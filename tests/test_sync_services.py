from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.accounting import repository
from app.common import bridge
from app.common import services


@pytest.mark.parametrize("insert_server_event", [False, True])
async def test_sync_event_insert_compiles_payload_as_jsonb(insert_server_event):
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value="event-id")))

    if insert_server_event:
        await services.queue_server_event(
            session=session,
            business_id="tenant-id",
            tenant_id="tenant-id",
            event_type="sale.created",
            payload={"sale_id": "sale-id"},
        )
    else:
        await services.process_sync_batch(
            session=session,
            business_id="tenant-id",
            user_id="user-id",
            events=[
                {
                    "event_type": "legacy",
                    "client_id": None,
                    "client_ts": None,
                    "payload": {"sale_id": "sale-id"},
                }
            ],
        )

    statement = session.execute.call_args_list[0].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "payload" in compiled.params
    assert ":payload" not in str(compiled)
    assert "CAST(" in str(compiled)


@pytest.mark.parametrize(
    ("status", "set_applied_at", "increment_retry"),
    [
        ("pending", False, False),
        ("processed", True, False),
        ("failed", True, True),
    ],
)
async def test_mark_sync_event_uses_separately_typed_status_flags(
    status, set_applied_at, increment_retry
):
    session = MagicMock()
    session.execute = AsyncMock()

    await services._mark_sync_event(
        session,
        "event-id",
        status=status,
        error="event error" if status == "failed" else None,
    )

    statement, parameters = session.execute.call_args.args
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "status" in compiled.params
    assert "set_applied_at" in compiled.params
    assert "increment_retry" in compiled.params
    assert parameters["set_applied_at"] is set_applied_at
    assert parameters["increment_retry"] is increment_retry
    assert "IN ('processed','failed')" not in str(statement)


async def test_settle_sale_receivable_resolves_accounts(monkeypatch):
    from types import SimpleNamespace

    from app.accounting import rpc
    from app.common.db import session as db_session

    tenant_id = uuid4()
    sale_id = uuid4()
    account = SimpleNamespace(id=uuid4(), code="1000")
    receivable = SimpleNamespace(
        balance=5,
        amount_paid=0,
        status="pending",
    )
    scalar_result = MagicMock(first=MagicMock(return_value=receivable))
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=scalar_result))
    )
    set_rls_context = AsyncMock()
    get_account_by_code = AsyncMock(return_value=account)
    post_journal = AsyncMock()
    monkeypatch.setattr(db_session, "set_rls_context", set_rls_context)
    monkeypatch.setattr(repository, "get_account_by_code", get_account_by_code)
    monkeypatch.setattr(rpc, "post_journal", post_journal)

    await bridge._settle_sale_receivable(
        session,
        business_id=str(tenant_id),
        sale=SimpleNamespace(
            id=sale_id,
            sale_number="SALE-1",
            store_id=None,
        ),
        amount=5,
        method="cash",
    )

    get_account_by_code.assert_awaited()
    post_journal.assert_awaited_once()
    assert receivable.amount_paid == 5
    assert receivable.balance == 0
    assert receivable.status == "paid"
