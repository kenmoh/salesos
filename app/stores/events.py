from uuid import UUID

from common.events import EventEnvelope
from common.events.names import STORE_CREATED


def store_created_event(
    *,
    tenant_id: UUID,
    store_id: UUID,
    name: str,
    is_warehouse: bool,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=STORE_CREATED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "store_id": str(store_id),
            "name": name,
            "is_warehouse": is_warehouse,
        },
    )
