from uuid import UUID

from pydantic import BaseModel, Field


class TenantCreateCommand(BaseModel):
    actor_id: UUID | None = None
    business_name: str = Field(..., min_length=2, max_length=120)
    business_email: str
    owner_name: str = Field(..., min_length=2, max_length=100)
    owner_email: str
    owner_phone: str | None = None
    owner_password_hash: str
    tier: str = "starter"
    correlation_id: str | None = None


class TenantResult(BaseModel):
    tenant_id: UUID
    slug: str
    subdomain: str
    business_name: str
    tier: str
    status: str
    owner_user_id: UUID


class TierChangeCommand(BaseModel):
    tenant_id: UUID
    actor_id: UUID | None = None
    new_tier: str
    correlation_id: str | None = None


TIER_LIMITS: dict[str, dict] = {
    "starter": {"max_terminals": 1, "max_products": 100, "max_users": 2},
    "growth": {"max_terminals": 3, "max_products": 1000, "max_users": 10},
    "pro": {"max_terminals": 10, "max_products": 999999, "max_users": 50},
}
