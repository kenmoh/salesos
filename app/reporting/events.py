from uuid import UUID

from app.common.events import EventEnvelope
from app.common.events.names import REPORTING_PROJECTION_UPDATED


def projection_updated_event(
    *,
    tenant_id: UUID,
    projection_type: str,
    projection_id: UUID,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=REPORTING_PROJECTION_UPDATED,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "projection_type": projection_type,
            "projection_id": str(projection_id),
        },
    )
