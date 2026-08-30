from uuid import UUID

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    User,
    UserRole,
    Role,
    Permission,
    RolePermission,
    PasswordResetToken,
    AuthAuditLog,
)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Get user by email address.

    Args:
        session: Database session.
        email: User's email address.

    Returns:
        User if found, None otherwise.
    """
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    """Get user by unique identifier.

    Args:
        session: Database session.
        user_id: User's unique identifier.

    Returns:
        User if found, None otherwise.
    """
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_users_by_tenant(session: AsyncSession, tenant_id: UUID) -> list[User]:
    """Get all users for a tenant.

    Args:
        session: Database session.
        tenant_id: Tenant's unique identifier.

    Returns:
        List of users ordered by creation date (newest first).
    """
    result = await session.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


async def create_user(session: AsyncSession, user: User) -> User:
    """Create a new user.

    Args:
        session: Database session.
        user: User instance to persist.

    Returns:
        Created user with generated fields.
    """
    session.add(user)
    await session.flush()
    return user


async def update_user_totp(
    session: AsyncSession, user_id: UUID, secret: str | None, enabled: bool
) -> None:
    """Update user's TOTP settings.

    Args:
        session: Database session.
        user_id: User's unique identifier.
        secret: TOTP secret key (None to disable).
        enabled: Whether TOTP is enabled.
    """
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.totp_secret = secret
    user.totp_enabled = 1 if enabled else 0
    await session.flush()


async def create_password_reset_token(session: AsyncSession, token: PasswordResetToken) -> None:
    """Create a password reset token.

    Args:
        session: Database session.
        token: PasswordResetToken instance to persist.
    """
    session.add(token)
    await session.flush()


async def get_valid_reset_token(
    session: AsyncSession, token_hash: str
) -> PasswordResetToken | None:
    """Get a valid (non-expired) password reset token.

    Args:
        session: Database session.
        token_hash: Hashed token value.

    Returns:
        PasswordResetToken if found and valid, None otherwise.
    """
    from datetime import UTC, datetime

    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.expires_at > datetime.now(UTC),
        )
    )
    return result.scalar_one_or_none()


async def log_auth_event(session: AsyncSession, log: AuthAuditLog) -> None:
    """Log an authentication event.

    Args:
        session: Database session.
        log: AuthAuditLog instance to persist.
    """
    session.add(log)
    await session.flush()


async def get_recent_login_attempts(
    session: AsyncSession,
    user_id: UUID,
    limit: int = 10,
) -> list[AuthAuditLog]:
    """Get recent login attempts for a user.

    Args:
        session: Database session.
        user_id: User's unique identifier.
        limit: Maximum number of records to return.

    Returns:
        List of auth audit logs ordered by creation date (newest first).
    """
    result = await session.execute(
        select(AuthAuditLog)
        .where(
            AuthAuditLog.user_id == user_id,
            AuthAuditLog.action.in_(["login_success", "login_failed"]),
        )
        .order_by(AuthAuditLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_failed_login_count(
    session: AsyncSession,
    user_id: UUID,
    minutes: int = 15,
) -> int:
    """Count failed login attempts in the last N minutes.

    Args:
        session: Database session.
        user_id: User's unique identifier.
        minutes: Time window in minutes (default: 15).

    Returns:
        Number of failed login attempts.
    """
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    result = await session.execute(
        select(AuthAuditLog)
        .where(
            AuthAuditLog.user_id == user_id,
            AuthAuditLog.action == "login_failed",
            AuthAuditLog.created_at >= cutoff,
        )
    )
    return len(list(result.scalars().all()))


async def get_unique_ips_for_user(
    session: AsyncSession,
    user_id: UUID,
    days: int = 30,
) -> list[str]:
    """Get unique IP addresses used by a user in the last N days.

    Args:
        session: Database session.
        user_id: User's unique identifier.
        days: Time window in days (default: 30).

    Returns:
        List of unique IP addresses.
    """
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(AuthAuditLog.ip_address)
        .where(
            AuthAuditLog.user_id == user_id,
            AuthAuditLog.action == "login_success",
            AuthAuditLog.created_at >= cutoff,
            AuthAuditLog.ip_address.isnot(None),
        )
        .distinct()
    )
    return [row[0] for row in result.all()]


async def list_audit_logs(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    action: str | None = None,
    user_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuthAuditLog]:
    """List audit logs for a tenant, optionally filtered by action or user.

    Args:
        session: Database session.
        tenant_id: Tenant unique identifier.
        action: Optional action filter (e.g. 'login_success', 'cart_void_approved').
        user_id: Optional user filter.
        limit: Max records to return (default 50).
        offset: Pagination offset.

    Returns:
        List of AuthAuditLog ordered by created_at desc.
    """
    stmt = select(AuthAuditLog).where(AuthAuditLog.tenant_id == tenant_id)
    if action:
        stmt = stmt.where(AuthAuditLog.action == action)
    if user_id:
        stmt = stmt.where(AuthAuditLog.user_id == user_id)
    stmt = stmt.order_by(AuthAuditLog.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
    """Get role by name (system-wide).

    Args:
        session: Database session.
        role_name: Role name.

    Returns:
        Role if found, None otherwise.
    """
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalar_one_or_none()


async def get_all_roles(session: AsyncSession) -> list[Role]:
    """Get all system-wide roles.

    Args:
        session: Database session.

    Returns:
        List of roles ordered by rank (highest first).
    """
    result = await session.execute(select(Role).order_by(Role.rank.desc()))
    return list(result.scalars().all())


async def get_assignable_roles(session: AsyncSession) -> list[Role]:
    """Get roles that can be assigned to users.

    Args:
        session: Database session.

    Returns:
        List of roles with rank between 20-80.
    """
    result = await session.execute(
        select(Role).where(Role.rank.between(20, 80)).order_by(Role.rank.desc())
    )
    return list(result.scalars().all())


async def get_permissions_for_role(session: AsyncSession, role_id: UUID) -> list[Permission]:
    """Get permissions assigned to a role.

    Args:
        session: Database session.
        role_id: Role's unique identifier.

    Returns:
        List of permissions.
    """
    result = await session.execute(
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return list(result.scalars().all())


async def get_effective_permissions(session: AsyncSession, user_id: UUID) -> list[Permission]:
    """Get all permissions effective for a user (via roles).

    Args:
        session: Database session.
        user_id: User's unique identifier.

    Returns:
        List of unique permissions across all user roles.
    """
    result = await session.execute(
        select(Permission)
        .distinct()
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    return list(result.scalars().all())


async def assign_role_to_user(
    session: AsyncSession, user_id: UUID, role_id: UUID, assigned_by: UUID | None = None
) -> UserRole:
    """Assign a role to a user.

    Args:
        session: Database session.
        user_id: User's unique identifier.
        role_id: Role's unique identifier.
        assigned_by: Optional user who assigned the role.

    Returns:
        Created UserRole instance.
    """
    ur = UserRole(user_id=user_id, role_id=role_id, assigned_by=assigned_by)
    session.add(ur)
    await session.flush()
    return ur


async def remove_role_from_user(session: AsyncSession, user_id: UUID, role_id: UUID) -> None:
    """Remove a role from a user.

    Args:
        session: Database session.
        user_id: User's unique identifier.
        role_id: Role's unique identifier.
    """
    from sqlalchemy import delete

    await session.execute(
        delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
    )
    await session.flush()


async def get_user_roles(session: AsyncSession, user_id: UUID) -> list[Role]:
    """Get all roles assigned to a user.

    Args:
        session: Database session.
        user_id: User's unique identifier.

    Returns:
        List of roles ordered by rank (highest first).
    """
    result = await session.execute(
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.rank.desc())
    )
    return list(result.scalars().all())


async def get_all_permissions(session: AsyncSession) -> list[Permission]:
    """Get all permissions in the system.

    Args:
        session: Database session.

    Returns:
        List of permissions ordered by name.
    """
    result = await session.execute(select(Permission).order_by(Permission.name))
    return list(result.scalars().all())


async def get_role_by_id(session: AsyncSession, role_id: UUID) -> Role | None:
    """Get role by unique identifier.

    Args:
        session: Database session.
        role_id: Role's unique identifier.

    Returns:
        Role if found, None otherwise.
    """
    result = await session.execute(select(Role).where(Role.id == role_id))
    return result.scalar_one_or_none()


async def get_roles_by_tenant(session: AsyncSession, tenant_id: UUID) -> list[Role]:
    """Get all roles for a tenant.

    Args:
        session: Database session.
        tenant_id: Tenant's unique identifier.

    Returns:
        List of roles ordered by rank (highest first).
    """
    result = await session.execute(
        select(Role).where(Role.tenant_id == tenant_id).order_by(Role.rank.desc())
    )
    return list(result.scalars().all())


async def get_role_by_name_for_tenant(
    session: AsyncSession, tenant_id: UUID, name: str
) -> Role | None:
    """Get role by name for a specific tenant.

    Args:
        session: Database session.
        tenant_id: Tenant's unique identifier.
        role_name: Role name.

    Returns:
        Role if found, None otherwise.
    """
    result = await session.execute(
        select(Role).where(Role.tenant_id == tenant_id, Role.name == name)
    )
    return result.scalar_one_or_none()


async def create_role(
    session: AsyncSession,
    tenant_id: UUID,
    name: str,
    rank: int,
    description: str | None = None,
) -> Role:
    """Create a new role.

    Args:
        session: Database session.
        tenant_id: Tenant's unique identifier.
        name: Role name.
        rank: Role rank for hierarchy.
        description: Optional role description.

    Returns:
        Created Role instance.
    """
    role = Role(tenant_id=tenant_id, name=name, rank=rank, description=description)
    session.add(role)
    await session.flush()
    return role


async def update_role(
    session: AsyncSession,
    role_id: UUID,
    name: str | None = None,
    rank: int | None = None,
    description: str | None = None,
) -> Role | None:
    """Update an existing role.

    Args:
        session: Database session.
        role_id: Role's unique identifier.
        name: New role name.
        rank: New role rank.
        description: New role description.

    Returns:
        Updated Role if found, None otherwise.
    """
    role = await get_role_by_id(session, role_id)
    if not role:
        return None
    if name is not None:
        role.name = name
    if rank is not None:
        role.rank = rank
    if description is not None:
        role.description = description
    await session.flush()
    return role


async def delete_role(session: AsyncSession, role_id: UUID) -> bool:
    """Delete a role.

    Args:
        session: Database session.
        role_id: Role's unique identifier.

    Returns:
        True if deleted, False if not found.
    """
    role = await get_role_by_id(session, role_id)
    if not role:
        return False
    await session.delete(role)
    await session.flush()
    return True


async def set_role_permissions(
    session: AsyncSession, role_id: UUID, permission_ids: list[UUID]
) -> None:
    """Set permissions for a role (replaces all existing).

    Args:
        session: Database session.
        role_id: Role's unique identifier.
        permission_ids: List of permission identifiers to assign.
    """
    await session.execute(sa_delete(RolePermission).where(RolePermission.role_id == role_id))
    for pid in permission_ids:
        session.add(RolePermission(role_id=role_id, permission_id=pid))
    await session.flush()
