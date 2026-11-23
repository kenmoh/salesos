from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

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
