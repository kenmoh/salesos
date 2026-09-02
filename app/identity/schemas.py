from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserCreateCommand(BaseModel):
    tenant_id: UUID
    actor_id: UUID | None = None
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str | None = None
    password_hash: str
    correlation_id: str | None = None


class UserResult(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    role: str
    status: str
    totp_enabled: bool
    auto_create_cart: bool
    last_login_at: str | None


class RoleResponse(BaseModel):
    id: UUID
    name: str
    rank: int
    description: str | None


class PermissionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None


class UserRoleAssignCommand(BaseModel):
    user_id: UUID
    role_name: str
    actor_id: UUID | None = None
    correlation_id: str | None = None


class UserRoleRemoveCommand(BaseModel):
    user_id: UUID
    role_name: str
    correlation_id: str | None = None
