import re
from uuid import uuid4

from common.events.outbox import OutboxWrite
from tenancy.events import tenant_created_event, tier_changed_event
from tenancy.models import Tenant, TenantTierProjection
from tenancy.schemas import TenantCreateCommand, TenantResult, TIER_LIMITS

SUBDOMAIN_SUFFIX = ".storeflow.ng"


def slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug[:80]


def plan_tenant_creation(
    command: TenantCreateCommand, slug: str
) -> tuple[TenantResult, Tenant, list[OutboxWrite]]:
    """Create tenant + owner user + owner role in one plan."""
    tenant_id = uuid4()
    owner_user_id = uuid4()
    subdomain = f"{slug}{SUBDOMAIN_SUFFIX}"

    tenant = Tenant(
        id=tenant_id,
        slug=slug,
        subdomain=subdomain,
        business_name=command.business_name,
        business_email=command.business_email,
        owner_name=command.owner_name,
        owner_email=command.owner_email,
        owner_phone=command.owner_phone,
        tier=command.tier,
        status="active",
    )

    from identity.service import plan_user_creation
    from identity.schemas import UserCreateCommand

    user_command = UserCreateCommand(
        tenant_id=tenant_id,
        actor_id=command.actor_id,
        email=command.owner_email,
        full_name=command.owner_name,
        phone=command.owner_phone,
        password_hash=command.owner_password_hash,
        correlation_id=command.correlation_id,
    )
    user_result, user_model, user_outbox = plan_user_creation(user_command)

    event = tenant_created_event(
        tenant_id=tenant_id,
        actor_id=command.actor_id,
        slug=slug,
        business_name=command.business_name,
        owner_email=command.owner_email,
        tier=command.tier,
        correlation_id=command.correlation_id,
    )

    result = TenantResult(
        tenant_id=tenant_id,
        slug=slug,
        subdomain=subdomain,
        business_name=command.business_name,
        tier=command.tier,
        status="active",
        owner_user_id=owner_user_id,
    )

    outbox = [OutboxWrite(event=event, aggregate_type="tenant", aggregate_id=str(tenant_id))]
    outbox.extend(user_outbox)

    return result, tenant, user_model, outbox


def plan_tier_change(
    *,
    tenant_id: str,
    old_tier: str,
    new_tier: str,
    actor_id: str | None,
    correlation_id: str | None,
) -> tuple[list[OutboxWrite], TenantTierProjection]:
    limits = TIER_LIMITS.get(new_tier, TIER_LIMITS["starter"])
    projection = TenantTierProjection(
        tenant_id=tenant_id,
        tier=new_tier,
        max_terminals=limits["max_terminals"],
        max_products=limits["max_products"],
        max_users=limits["max_users"],
    )
    event = tier_changed_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        old_tier=old_tier,
        new_tier=new_tier,
        correlation_id=correlation_id,
    )
    outbox = [OutboxWrite(event=event, aggregate_type="tenant", aggregate_id=str(tenant_id))]
    return outbox, projection
