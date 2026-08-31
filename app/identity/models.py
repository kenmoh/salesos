"""Identity service database models.

This module defines the models for user authentication and authorization:
- User: User accounts with authentication details
- UserRole: User-role assignment junction table
- Role: Role definitions with permissions
- Permission: Granular permission definitions
- RolePermission: Role-permission assignment junction table
- PasswordResetToken: Password reset request tokens
- AuthAuditLog: Authentication event audit trail
- SupervisorPin: Supervisor override PINs with expiry (7-day TTL)

The identity system provides:
- Multi-factor authentication (TOTP)
- Role-based access control (RBAC)
- Audit logging for security
- Supervisor PIN override for cart voids
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.base import StoreFlowBase


class User(StoreFlowBase):
    """Represents a user account in the system.

    Users are associated with a tenant and can have multiple roles.
    The system supports TOTP-based multi-factor authentication.

    Status values:
    - pending: Account awaiting activation
    - active: Account is active
    - inactive: Account has been deactivated
    - suspended: Account has been suspended

    Attributes:
        tenant_identifier: Reference to the tenant.
        email: Unique email address for login.
        password_hash: Hashed password (bcrypt).
        full_name: User's full name.
        phone: Optional phone number.
        status: Account status.
        totp_secret: TOTP secret for MFA (if enabled).
        totp_enabled: Whether TOTP is enabled.
        last_login_at: Timestamp of last successful login.
        avatar_url: Optional profile picture URL.
    """

    __tablename__ = "users"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    totp_secret: Mapped[str | None] = mapped_column(String(32), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class UserRole(StoreFlowBase):
    """Junction table for user-role assignments.

    Links users to roles with tracking of who assigned the role.

    Attributes:
        user_identifier: Reference to the user.
        role_identifier: Reference to the role.
        assigned_identifier: User who assigned this role.
        assigned_at: Timestamp of role assignment.
    """

    __tablename__ = "user_roles"


    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Role(StoreFlowBase):
    """Represents a role with associated permissions.

    Roles can be tenant-specific or system-wide. Each role has a
    rank for hierarchy and a set of permissions.

    Attributes:
        tenant_identifier: Reference to the tenant (null for system roles).
        name: Role name (e.g., "admin", "cashier", "manager").
        rank: Numeric rank for role hierarchy.
        description: Optional role description.
        permissions: List of associated Permission objects.
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_role_tenant_name"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permissions: Mapped[list["Permission"]] = relationship(secondary="role_permissions")


class Permission(StoreFlowBase):
    """Represents a granular permission in the system.

    Permissions are string-based (e.g., "sales:create", "products:read")
    and are assigned to roles.

    Attributes:
        name: Unique permission name (resource:action format).
        description: Optional permission description.
    """

    __tablename__ = "permissions"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RolePermission(StoreFlowBase):
    """Junction table for role-permission assignments.

    Attributes:
        role_identifier: Reference to the role.
        permission_identifier: Reference to the permission.
    """

    __tablename__ = "role_permissions"


    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class PasswordResetToken(StoreFlowBase):
    """Token for password reset requests.

    Tokens are hashed before storage and expire after a configured period.

    Attributes:
        user_identifier: Reference to the user requesting reset.
        token_hash: Hashed token (bcrypt).
        expires_at: When the token expires.
        ip_address: IP address of the request.
    """

    __tablename__ = "password_reset_tokens"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AuthAuditLog(StoreFlowBase):
    """Audit trail for authentication events.

    Records all significant authentication actions for security
    monitoring and compliance.

    Attributes:
        user_identifier: Reference to the user (if applicable).
        tenant_identifier: Reference to the tenant.
        action: Action type (e.g., "login_success", "login_failed", "password_reset").
        ip_address: Client IP address.
        user_agent: Client user agent string.
        details: Additional action details as JSON.
    """

    __tablename__ = "auth_audit_logs"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[dict | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class SupervisorPin(StoreFlowBase):
    """Stores supervisor override PINs with 7-day expiry.

    Used for cart item void overrides when a cashier lacks cart:delete permission.
    The PIN belongs to a user with cart:delete permission (owner/manager).
    Expires after 7 days — owner can force-regenerate via API.

    Attributes:
        user_id: Reference to the supervisor user (PK).
        pin_hash: Bcrypt-hashed 4-6 digit PIN.
        expires_at: When the PIN expires (7 days from creation).
        created_at: When the PIN was created.
    """

    __tablename__ = "supervisor_pins"


    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class IPBan(StoreFlowBase):
    """Represents an IP address banned from a tenant.

    Used by the IP filter middleware and admin security endpoints to block
    abusive or unauthorised client IP addresses.

    Attributes:
        ip: Banned client IP address.
        tenant_id: Tenant the ban applies to.
        banned_by: User who created the ban.
        reason: Optional reason for the ban.
        banned_at: When the ban was created.
        expires_at: When the ban expires (NULL = permanent).
    """

    __tablename__ = "ip_bans"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    banned_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    banned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("ip", "tenant_id", name="uq_ip_bans_ip_tenant"),)
