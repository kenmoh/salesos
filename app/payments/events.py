"""Payments service - owns payment intents, records, and webhook processing."""

from uuid import UUID

from common.events import EventEnvelope
from common.events.names import PAYMENT_FAILED, PAYMENT_INTENT_CREATED, PAYMENT_SUCCEEDED


def payment_intent_created_event(
    *,
    tenant_id: UUID,
    payment_id: UUID,
    sale_id: str,
    amount: str,
    method: str,
    reference: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=PAYMENT_INTENT_CREATED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "payment_id": str(payment_id),
            "sale_id": sale_id,
            "amount": amount,
            "method": method,
            "reference": reference,
        },
    )


def payment_succeeded_event(
    *,
    tenant_id: UUID,
    payment_id: UUID,
    sale_id: str,
    amount: str,
    method: str,
    reference: str,
    gateway_reference: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=PAYMENT_SUCCEEDED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "payment_id": str(payment_id),
            "sale_id": sale_id,
            "amount": amount,
            "method": method,
            "reference": reference,
            "gateway_reference": gateway_reference,
        },
    )


def payment_failed_event(
    *,
    tenant_id: UUID,
    payment_id: UUID,
    sale_id: str,
    reason: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=PAYMENT_FAILED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "payment_id": str(payment_id),
            "sale_id": sale_id,
            "reason": reason,
        },
    )
