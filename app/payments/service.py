import uuid
from datetime import UTC, datetime, timedelta


from common.events.outbox import OutboxWrite
from payments.events import (
    payment_intent_created_event,
    payment_succeeded_event,
    payment_failed_event,
)
from payments.models import PaymentIntent
from payments.schemas import PaymentIntentCreateCommand, PaymentIntentResult


def plan_payment_intent(
    command: PaymentIntentCreateCommand,
) -> tuple[PaymentIntentResult, PaymentIntent, list[OutboxWrite]]:
    payment_id = uuid.uuid4()
    reference = f"SF-{uuid.uuid4().hex[:16].upper()}"
    expires = datetime.now(UTC) + timedelta(minutes=30)

    intent = PaymentIntent(
        id=payment_id,
        tenant_id=command.tenant_id,
        sale_id=command.sale_id,
        method=command.method,
        amount=float(command.amount),
        currency=command.currency,
        status="pending",
        gateway_reference=reference,
        expires_at=expires,
        intent_metadata=command.metadata,
    )

    event = payment_intent_created_event(
        tenant_id=command.tenant_id,
        payment_id=payment_id,
        sale_id=str(command.sale_id),
        amount=str(command.amount),
        method=command.method,
        reference=reference,
        correlation_id=command.correlation_id,
    )

    result = PaymentIntentResult(
        payment_id=payment_id,
        reference=reference,
        authorization_url=None,
        access_code=None,
        amount=command.amount,
        expires_at=expires.isoformat(),
    )

    outbox = [
        OutboxWrite(event=event, aggregate_type="payment_intent", aggregate_id=str(payment_id))
    ]
    return result, intent, outbox


def plan_payment_success(
    *,
    tenant_id: uuid.UUID,
    payment_id: uuid.UUID,
    sale_id: str,
    amount: str,
    method: str,
    reference: str,
    gateway_reference: str | None = None,
    correlation_id: str | None = None,
) -> list[OutboxWrite]:
    event = payment_succeeded_event(
        tenant_id=tenant_id,
        payment_id=payment_id,
        sale_id=sale_id,
        amount=amount,
        method=method,
        reference=reference,
        gateway_reference=gateway_reference,
        correlation_id=correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="payment", aggregate_id=str(payment_id))]


def plan_payment_failure(
    *,
    tenant_id: uuid.UUID,
    payment_id: uuid.UUID,
    sale_id: str,
    reason: str,
    correlation_id: str | None = None,
) -> list[OutboxWrite]:
    event = payment_failed_event(
        tenant_id=tenant_id,
        payment_id=payment_id,
        sale_id=sale_id,
        reason=reason,
        correlation_id=correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="payment", aggregate_id=str(payment_id))]



