from uuid import UUID

from app.common.events import EventEnvelope
from app.common.events.names import (
    CATALOG_PRODUCT_CREATED as PRODUCT_CREATED,
    CATALOG_PRODUCT_QR_GENERATED as PRODUCT_QR_GENERATED,
)


def product_created_event(
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    product_id: UUID,
    public_id: str,
    name: str,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=PRODUCT_CREATED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "product_id": str(product_id),
            "public_id": public_id,
            "name": name,
        },
    )


def product_qr_generated_event(
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    product_id: UUID,
    public_id: str,
    qr_payload: str,
    qr_asset_url: str | None,
    correlation_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=PRODUCT_QR_GENERATED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "product_id": str(product_id),
            "public_id": public_id,
            "qr_payload": qr_payload,
            "qr_asset_url": qr_asset_url,
        },
    )
