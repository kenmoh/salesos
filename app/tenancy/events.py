"""Tenancy service - owns tenant businesses, subscription tiers, and feature limits."""

from uuid import UUID

from common.events import EventEnvelope
from common.events.names import TENANT_CREATED, TENANT_TIER_CHANGED


def tenant_created_event(
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    slug: str,
    business_name: str,
    owner_email: str,
    tier: str = "starter",
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a tenant created event.

    Args:
        tenant_id: Unique tenant identifier.
        actor_id: User who created the tenant.
        slug: URL-friendly tenant identifier.
        business_name: Name of the business.
        owner_email: Email address of the business owner.
        tier: Subscription tier (default: starter).
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the tenant created event.
    """
    return EventEnvelope(
        event_type=TENANT_CREATED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "tenant_id": str(tenant_id),
            "slug": slug,
            "business_name": business_name,
            "owner_email": owner_email,
            "tier": tier,
        },
    )


def tier_changed_event(
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    old_tier: str,
    new_tier: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a tier changed event.

    Args:
        tenant_id: Unique tenant identifier.
        actor_id: User who changed the tier.
        old_tier: Previous subscription tier.
        new_tier: New subscription tier.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the tier changed event.
    """
    return EventEnvelope(
        event_type=TENANT_TIER_CHANGED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "tenant_id": str(tenant_id),
            "old_tier": old_tier,
            "new_tier": new_tier,
        },
    )
