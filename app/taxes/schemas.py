from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TaxCreateCommand(BaseModel):
    tenant_id: UUID | None = None
    name: str = Field(min_length=1, max_length=50)
    rate: float = Field(ge=0, le=100)
    #: Liability account this tax is owed to. Omitted means the default for
    #: its name -- VAT to 2300, everything else to 2400.
    account_code: str | None = Field(default=None, max_length=10)


class TaxUpdateCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    rate: float | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None
    account_code: str | None = Field(default=None, max_length=10)


class TaxResult(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    rate: float
    is_active: bool
    account_code: str | None = None
    created_at: datetime
    updated_at: datetime
