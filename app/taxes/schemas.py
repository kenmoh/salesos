from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TaxCreateCommand(BaseModel):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=50)
    rate: float = Field(ge=0, le=100)


class TaxUpdateCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    rate: float | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class TaxResult(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    rate: float
    is_active: bool
    created_at: datetime
    updated_at: datetime
