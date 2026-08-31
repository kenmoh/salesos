from uuid import UUID

from app.common.events import EventEnvelope
from app.common.events.names import NOTIFICATION_SENT, NOTIFICATION_FAILED


def notification_sent_event(
    *,
    tenant_id: UUID,
    notification_id: UUID,
    channel: str,
    recipient: str,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=NOTIFICATION_SENT,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "notification_id": str(notification_id),
            "channel": channel,
            "recipient": recipient,
        },
    )


def notification_failed_event(
    *,
    tenant_id: UUID,
    notification_id: UUID,
    channel: str,
    recipient: str,
    error: str,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=NOTIFICATION_FAILED,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "notification_id": str(notification_id),
            "channel": channel,
            "recipient": recipient,
            "error": error,
        },
    )
