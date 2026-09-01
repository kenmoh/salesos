from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.security import validate_password_strength


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(Base):
    business_name: str = Field(..., min_length=2, max_length=120)
    business_email: EmailStr
    owner_name: str = Field(..., min_length=2, max_length=100)
    owner_email: EmailStr
    owner_phone: str | None = None
    password: str
    confirm_password: str

    @field_validator("password")
    @classmethod
    def strong_pw(cls, value):
        errors = validate_password_strength(value)
        if errors:
            raise ValueError(" | ".join(errors))
        return value

    @model_validator(mode="after")
    def pw_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class LoginRequest(Base):
    email: EmailStr
    password: str
    totp_code: str | None = None
    device_name: str | None = None


class RefreshRequest(Base):
    refresh_token: str


class LogoutRequest(Base):
    refresh_token: str
    all_devices: bool = False


class ChangePasswordRequest(Base):
    current_password: str
    new_password: str
    confirm_password: str
    revoke_other_sessions: bool = True

    @field_validator("new_password")
    @classmethod
    def strong_pw(cls, value):
        errors = validate_password_strength(value)
        if errors:
            raise ValueError(" | ".join(errors))
        return value

    @model_validator(mode="after")
    def check(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        if self.new_password == self.current_password:
            raise ValueError("Must differ from current")
        return self


class PasswordResetRequest(Base):
    email: EmailStr


class PasswordResetComplete(Base):
    token: str
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def strong_reset_pw(cls, value):
        errors = validate_password_strength(value)
        if errors:
            raise ValueError(" | ".join(errors))
        return value

    @model_validator(mode="after")
    def pw_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class VerifyEmailRequest(Base):
    token: str


class TOTPVerifyRequest(Base):
    code: str = Field(..., min_length=6, max_length=6)


class TOTPDisableRequest(Base):
    password: str
    code: str = Field(..., min_length=6, max_length=6)


class CreateEmployeeRequest(Base):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str | None = None
    role: str = Field(default="viewer", min_length=1)
    password: str | None = None
    store_id: UUID | None = None


class UpdateRoleRequest(Base):
    user_id: UUID
    new_role: str


class RoleCreateRequest(Base):
    name: str = Field(..., min_length=1, max_length=30)
    rank: int = Field(default=50, ge=1, le=100)
    description: str | None = Field(default=None, max_length=255)
    permission_ids: list[str] = []


class RoleUpdateRequest(Base):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    rank: int | None = Field(default=None, ge=1, le=100)
    description: str | None = Field(default=None, max_length=255)


class RoleSetPermissionsRequest(Base):
    permission_ids: list[str]


class UserProfile(Base):
    user_id: UUID
    business_id: UUID
    email: EmailStr
    full_name: str
    role: str
    status: str
    permissions: list[str]
    totp_enabled: bool
    last_login_at: datetime | None
    avatar_url: str | None


class TokenPair(Base):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class LoginResponse(Base):
    tokens: TokenPair
    user: UserProfile
    requires_totp: bool = False


class SessionInfo(Base):
    session_id: str
    device_name: str | None
    device_type: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime


class EmployeeListItem(Base):
    user_id: UUID
    email: EmailStr
    full_name: str
    phone: str | None
    role: str
    status: str
    last_login_at: datetime | None
    created_at: datetime


class Msg(Base):
    message: str
    success: bool = True
