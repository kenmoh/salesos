from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ProductCreateCommand(BaseModel):
    tenant_id: UUID
    actor_id: UUID | None = None
    tenant_slug: str = Field(..., min_length=3, max_length=80)
    name: str = Field(..., min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    category_id: UUID | None = None
    selling_price: Decimal = Field(..., ge=0)
    correlation_id: str | None = None


class ProductCreateResult(BaseModel):
    product_id: UUID
    public_id: str
    qr_payload: str
    qr_url: str | None = None


class CategoryCreateCommand(BaseModel):
    store_id: UUID
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = None


class CategoryCreateResult(BaseModel):
    category_id: UUID
    name: str


class CategoryResponse(BaseModel):
    id: UUID
    store_id: UUID
    name: str
    description: str | None
    created_at: datetime
