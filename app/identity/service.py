from uuid import UUID, uuid4

from app.common.events.outbox import OutboxWrite
from app.identity.events import user_created_event, role_changed_event
from app.identity.models import User
from app.identity.schemas import UserCreateCommand, UserResult


def plan_user_creation(command: UserCreateCommand) -> tuple[UserResult, User, list[OutboxWrite]]:
    """Plan user creation with event publishing.

    Args:
        command: User creation command with user details.

    Returns:
        Tuple of (result, user, outbox_events) for persistence.
    """
    user_id = uuid4()
    user = User(
        id=user_id,
        tenant_id=command.tenant_id,
        email=command.email,
        full_name=command.full_name,
        phone=command.phone,
        password_hash=command.password_hash,
        status="active",
    )
    event = user_created_event(
        tenant_id=command.tenant_id,
        user_id=user_id,
        email=command.email,
        full_name=command.full_name,
        role="",
        correlation_id=command.correlation_id,
    )
    result = UserResult(
        user_id=user_id,
        tenant_id=command.tenant_id,
        email=command.email,
        full_name=command.full_name,
        role="",
        status="active",
        totp_enabled=False,
        auto_create_cart=False,
        last_login_at=None,
    )
    outbox = [OutboxWrite(event=event, aggregate_type="user", aggregate_id=str(user_id))]
    return result, user, outbox


def plan_role_assignment(
    *,
    tenant_id: UUID,
    user_id: UUID,
    role_name: str,
    actor_id: UUID | None,
    correlation_id: str | None,
) -> list[OutboxWrite]:
    """Plan role assignment with event publishing.

    Args:
        tenant_id: Business/tenant identifier.
        user_id: User to assign role to.
        role_name: Role name to assign.
        actor_id: User performing the assignment.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        List of OutboxWrite events for persistence.
    """
    event = role_changed_event(
        tenant_id=tenant_id,
        user_id=user_id,
        old_role="",
        new_role=role_name,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="user", aggregate_id=str(user_id))]
