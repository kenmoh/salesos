"""Identity service - owns users, sessions, passwords, TOTP, roles, and permissions."""

from uuid import UUID

from common.events import EventEnvelope
from common.events.names import (
    IDENTITY_USER_CREATED,
    IDENTITY_ROLE_CHANGED,
    IDENTITY_SESSION_REVOKED,
)


def user_created_event(
    *,
    tenant_id: UUID,
    user_id: UUID,
    email: str,
    full_name: str,
    role: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a user created event.

    Args:
        tenant_id: Business/tenant identifier.
        user_id: Unique user identifier.
        email: User's email address.
        full_name: User's full name.
        role: Initial role assigned to the user.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the user created event.
    """
    return EventEnvelope(
        event_type=IDENTITY_USER_CREATED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "user_id": str(user_id),
            "email": email,
            "full_name": full_name,
            "role": role,
        },
    )


def role_changed_event(
    *,
    tenant_id: UUID,
    user_id: UUID,
    old_role: str,
    new_role: str,
    actor_id: UUID | None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a role changed event.

    Args:
        tenant_id: Business/tenant identifier.
        user_id: Unique user identifier.
        old_role: Previous role name.
        new_role: New role name.
        actor_id: User who changed the role.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the role changed event.
    """
    return EventEnvelope(
        event_type=IDENTITY_ROLE_CHANGED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "user_id": str(user_id),
            "old_role": old_role,
            "new_role": new_role,
        },
    )


def session_revoked_event(
    *,
    tenant_id: UUID,
    user_id: UUID,
    session_id: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a session revoked event.

    Args:
        tenant_id: Business/tenant identifier.
        user_id: Unique user identifier.
        session_id: Session identifier that was revoked.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the session revoked event.
    """
    return EventEnvelope(
        event_type=IDENTITY_SESSION_REVOKED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "user_id": str(user_id),
            "session_id": session_id,
        },
    )
