from uuid import UUID

from app.common.events.envelope import EventEnvelope
from app.common.events.names import ACCOUNTING_JOURNAL_POSTED, ACCOUNTING_JOURNAL_FAILED


def journal_posted_event(
    *,
    tenant_id: UUID,
    journal_id: UUID,
    journal_number: str,
    reference_type: str | None,
    reference_id: str | None,
    actor_id: UUID | None,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=ACCOUNTING_JOURNAL_POSTED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "journal_id": str(journal_id),
            "journal_number": journal_number,
            "reference_type": reference_type,
            "reference_id": reference_id,
        },
    )


def journal_failed_event(
    *,
    tenant_id: UUID,
    journal_id: UUID,
    journal_number: str,
    error: str,
    actor_id: UUID | None,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=ACCOUNTING_JOURNAL_FAILED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "journal_id": str(journal_id),
            "journal_number": journal_number,
            "error": error,
        },
    )
