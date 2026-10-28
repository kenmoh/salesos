import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.approval import require_supervisor_approval
from app.core.dependencies import TenantDep, DbTenantDep, get_db, require_permission
from app.core.ratelimit import auth_rate_limit
from app.core.responses import DataResponse, DataMessageResponse, ok
from app.auth.schemas.auth import (
    ChangePasswordRequest,
    CreateEmployeeRequest,
    DeleteAccountRequest,
    EmployeeStatusRequest,
    LoginRequest,
    LogoutRequest,
    PasswordResetComplete,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    RoleCreateRequest,
    RoleSetPermissionsRequest,
    RoleUpdateRequest,
    SocialSignInRequest,
    TOTPDisableRequest,
    TOTPVerifyRequest,
    UpdateEmployeeRequest,
    UpdateRoleRequest,
    VerifyEmailRequest,
)
from app.auth.schemas.responses import (
    AuditLogItem,
    AuthUser,
    EmployeeCreated,
    EmployeeListItem,
    LoginResponse,
    PermissionItem,
    PinStatusResponse,
    RegistrationResponse,
    RoleCreated,
    RoleDetail,
    SessionInfo,
    SuccessResponse,
    TokenPair,
    TOTPSetupResponse,
)
import app.common.auth_service as auth_service
from app.common.bridge import (
    create_tenant,
    create_role_for_tenant,
    delete_role_for_tenant,
    list_assignable_roles,
    list_all_roles,
    list_all_permissions,
    get_user_roles,
    assign_role,
    remove_role,
    set_role_permissions_for_tenant,
    update_role_for_tenant,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    status_code=201,
    response_model=DataMessageResponse[RegistrationResponse],
    dependencies=[Depends(auth_rate_limit(3, 3600))],
)
async def register(payload: RegisterRequest, request: Request):
    from app.core.security import hash_password

    # The tenant names its business in settings after sign-up; registration
    # only needs a placeholder to seed the tenant record and slug.
    business_name = (
        (payload.business_name or payload.owner_name or "").strip()
        or payload.business_email.split("@")[0].split("+")[0]
        or "My business"
    )

    tenant_result = await create_tenant(
        business_name=business_name,
        business_email=payload.business_email,
        owner_name=payload.owner_name,
        owner_email=payload.owner_email or payload.business_email,
        owner_phone=payload.owner_phone,
        owner_password_hash=hash_password(payload.password),
        actor_id=None,
        correlation_id=None,
    )

    return ok(
        {"tenant": tenant_result}, message="Registration successful. Check your email to verify."
    )


@router.post("/login", response_model=DataResponse[LoginResponse])
async def login(
    payload: LoginRequest,
    request: Request,
    session=Depends(get_db),
    _=Depends(auth_rate_limit(10, 900)),
):
    try:
        tokens, user = await auth_service.login(
            session=session,
            email=payload.email,
            password=payload.password,
            totp_code=payload.totp_code,
            req=request,
            device_name=payload.device_name,
        )
        return ok({"tokens": tokens, "user": user, "requires_totp": False})
    except auth_service.TotpRequired:
        return ok(
            {
                "tokens": {"access_token": "", "refresh_token": ""},
                "user": {},
                "requires_totp": True,
            }
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message)


@router.post("/social", response_model=DataResponse[LoginResponse])
async def social_sign_in(
    payload: SocialSignInRequest,
    request: Request,
    session=Depends(get_db),
    _=Depends(auth_rate_limit(20, 900)),
):
    try:
        tokens, user, is_new_user = await auth_service.login_with_social(
            session=session,
            id_token=payload.id_token,
            provider=payload.provider,
            req=request,
        )
        return ok({"tokens": tokens, "user": user, "requires_totp": False, "is_new_user": is_new_user})
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message)


@router.post("/refresh", response_model=DataResponse[TokenPair])
async def refresh(payload: RefreshRequest, request: Request, session=Depends(get_db)):
    return ok(
        await auth_service.refresh_tokens(
            session=session, raw_token=payload.refresh_token, req=request
        )
    )


@router.post(
    "/logout",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_sessions"))],
)
async def logout(payload: LogoutRequest, request: Request, ctx: DbTenantDep):
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    await auth_service.logout(
        session=ctx.session,
        access_token=token,
        raw_refresh=payload.refresh_token,
        user_id=ctx.user.user_id,
        all_devices=payload.all_devices,
    )
    return ok(None, message="Logged out")


@router.get(
    "/me",
    response_model=DataResponse[AuthUser],
    dependencies=[Depends(require_permission("auth:login"))],
)
async def me(ctx: DbTenantDep):
    from app.identity.models import User
    from sqlalchemy import select

    result = await ctx.session.execute(select(User).where(User.id == UUID(ctx.user.user_id)))
    user = result.scalar_one_or_none()
    if not user:
        return ok(
            {
                "user_id": ctx.user.user_id,
                "business_id": ctx.user.business_id,
                "email": "",
                "full_name": "",
                "role": ctx.user.role,
            "roles": ctx.user.roles,
                "status": "active",
                "permissions": ctx.user.permissions,
                "totp_enabled": False,
                "last_login_at": None,
                "avatar_url": None,
            }
        )
    return ok(
        {
            "user_id": str(user.id),
            "business_id": str(user.tenant_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": ctx.user.role,
            "roles": ctx.user.roles,
            "status": user.status,
            "permissions": ctx.user.permissions,
            "totp_enabled": bool(user.totp_enabled),
            "auto_create_cart": bool(user.auto_create_cart),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "avatar_url": user.avatar_url,
            "store_id": str(user.store_id) if user.store_id else None,
        }
    )


@router.patch(
    "/auto-create-cart",
    dependencies=[Depends(require_permission("auth:login"))],
)
async def toggle_auto_create_cart(ctx: DbTenantDep):
    from app.identity.models import User
    from sqlalchemy import select

    result = await ctx.session.execute(select(User).where(User.id == UUID(ctx.user.user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.auto_create_cart = 0 if user.auto_create_cart else 1
    await ctx.session.flush()
    return ok({"auto_create_cart": bool(user.auto_create_cart)})


@router.post("/verify-email", response_model=DataMessageResponse[SuccessResponse])
async def verify_email(payload: VerifyEmailRequest, session=Depends(get_db)):
    return ok(None, message="Email verified")


@router.post("/resend-verify", response_model=DataMessageResponse[SuccessResponse])
async def resend_verify(payload: PasswordResetRequest, session=Depends(get_db)):
    return ok(None, message="Verification email queued")


@router.post("/forgot-password", response_model=DataMessageResponse[SuccessResponse])
async def forgot_password(
    payload: PasswordResetRequest,
    request: Request,
    session=Depends(get_db),
    _=Depends(auth_rate_limit(3, 900)),
):
    await auth_service.forgot_password(session=session, email=payload.email, req=request)
    return ok(None, message="Password reset email queued")


@router.post("/reset-password", response_model=DataMessageResponse[SuccessResponse])
async def reset_password(
    payload: PasswordResetComplete,
    request: Request,
    session=Depends(get_db),
    _=Depends(auth_rate_limit(5, 900)),
):
    pw_ok = await auth_service.complete_reset(
        session=session, token=payload.token, new_pw=payload.new_password, req=request
    )
    return ok({"success": pw_ok}, message="Password reset" if pw_ok else "Invalid reset token")


@router.post(
    "/change-password",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    ctx: DbTenantDep,
    _=Depends(auth_rate_limit(5, 900)),
):
    await auth_service.change_password(
        session=ctx.session,
        user_id=ctx.user.user_id,
        cur_pw=payload.current_password,
        new_pw=payload.new_password,
        revoke_others=payload.revoke_other_sessions,
        cur_sid=None,
        req=request,
    )
    return ok(None, message="Password changed")


@router.delete(
    "/account",
    response_model=DataMessageResponse[None],
    dependencies=[Depends(auth_rate_limit(3, 3600))],
)
async def delete_account(
    request: Request,
    ctx: DbTenantDep,
    payload: DeleteAccountRequest | None = None,
):
    """Permanently delete the owner's business and every trace of it.

    Owner-only; password confirmation is required when the owner signed up
    with an email account. Members' refresh sessions and the caller's access
    token are revoked as part of the flow.
    """
    try:
        await auth_service.delete_own_account(
            session=ctx.session,
            user_id=ctx.user.user_id,
            business_id=ctx.user.business_id,
            role_names=ctx.user.roles,
            password=payload.password if payload else "",
            req=request,
            jti=ctx.user.jti,
            token_ttl=max(int(ctx.user.exp - time.time()), 0),
        )
    except auth_service.AuthError as exc:
        if exc.code == "password_required":
            raise HTTPException(status_code=428, detail=exc.message)
        raise HTTPException(status_code=400, detail=exc.message)
    return ok(None, message="Your account has been deleted")


@router.get(
    "/sessions",
    response_model=DataResponse[list[SessionInfo]],
    dependencies=[Depends(require_permission("auth:manage_sessions"))],
)
async def sessions(ctx: DbTenantDep):
    """Every active session in this tenant: who, on what device, last active.

    Owners hold auth:manage_sessions, so this is an owner-side screen: a user
    is signed in on one device at a time and anything stale here is a session
    an owner can revoke.
    """
    from sqlalchemy import text

    from app.core.security import list_all_sessions

    result = await ctx.session.execute(
        text("""
            SELECT u.id, u.email, u.full_name, u.status
            FROM users u
            WHERE u.tenant_id = :bid
        """),
        {"bid": ctx.user.business_id},
    )
    users = {
        str(r["id"]): {
            "email": r["email"],
            "full_name": r["full_name"],
            "status": r["status"],
        }
        for r in result.mappings().all()
    }
    sessions_list = await list_all_sessions(user_ids=list(users))
    return ok(
        [
            {
                "id": s["session_id"],
                "user_id": s["user_id"],
                "employee_name": users[s["user_id"]].get("full_name"),
                "email": users[s["user_id"]].get("email"),
                "status": users[s["user_id"]].get("status"),
                "device_name": s.get("device_name"),
                "user_agent": s.get("user_agent"),
                "ip_address": s.get("ip_address"),
                "created_at": s.get("created_at"),
                "last_active_at": s.get("last_active_at"),
            }
            for s in sessions_list
            if s.get("user_id") in users
        ]
    )


@router.delete(
    "/sessions/{session_id}",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_sessions"))],
)
async def revoke_session(session_id: str, ctx: TenantDep):
    from app.core.security import revoke_session as revoke

    await revoke(sid=session_id)
    return ok(None, message="Session revoked")


@router.post(
    "/totp/setup",
    response_model=DataResponse[TOTPSetupResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def totp_setup(ctx: DbTenantDep):
    from app.identity.models import User
    from sqlalchemy import select as sa_select

    result = await ctx.session.execute(
        sa_select(User).where(User.id == UUID(ctx.user.user_id))
    )
    user = result.scalar_one()
    return ok(
        await auth_service.setup_totp(
            session=ctx.session, user_id=ctx.user.user_id, email=user.email
        )
    )


@router.post(
    "/totp/verify",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def totp_verify(payload: TOTPVerifyRequest, ctx: DbTenantDep):
    await auth_service.enable_totp(session=ctx.session, user_id=ctx.user.user_id, code=payload.code)
    return ok(None, message="TOTP enabled")


@router.post(
    "/totp/disable",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def totp_disable(payload: TOTPDisableRequest, ctx: DbTenantDep):
    await auth_service.disable_totp(
        session=ctx.session, user_id=ctx.user.user_id, password=payload.password, code=payload.code
    )
    return ok(None, message="TOTP disabled")


class SetPinRequest(BaseModel):
    pin: str


@router.get(
    "/pin/status",
    response_model=DataMessageResponse[PinStatusResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def get_pin_status(ctx: DbTenantDep):
    from datetime import UTC, datetime
    from sqlalchemy import select as sa_select
    from app.identity.models import SupervisorPin

    now = datetime.now(UTC)
    result = await ctx.session.execute(
        sa_select(SupervisorPin).where(SupervisorPin.user_id == UUID(ctx.user.user_id))
    )
    pin = result.scalar_one_or_none()
    if not pin:
        return ok(PinStatusResponse(has_pin=False))
    return ok(PinStatusResponse(
        has_pin=True,
        expires_at=pin.expires_at.isoformat() if pin.expires_at else None,
    ))


@router.post(
    "/pin",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def set_supervisor_pin(payload: SetPinRequest, ctx: DbTenantDep):
    import re
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select as sa_select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.config import settings
    from app.core.security import hash_pin
    from app.identity.models import SupervisorPin

    pin = payload.pin.strip()
    if not re.match(r"^\d{4,6}$", pin):
        raise HTTPException(status_code=400, detail="PIN must be 4-6 digits")

    pin_hashed = hash_pin(pin)
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.supervisor_pin_expire_days)

    await ctx.session.execute(
        pg_insert(SupervisorPin)
        .values(
            user_id=UUID(ctx.user.user_id),
            pin_hash=pin_hashed,
            expires_at=expires_at,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={"pin_hash": pin_hashed, "expires_at": expires_at, "created_at": now},
        )
    )
    await ctx.session.commit()
    return ok(None, message="Supervisor PIN set")


@router.post(
    "/employees",
    status_code=201,
    response_model=DataResponse[EmployeeCreated],
    dependencies=[Depends(require_permission("employees:create"))],
)
async def create_employee(payload: CreateEmployeeRequest, request: Request, ctx: DbTenantDep):
    result = await auth_service.create_employee(
        session=ctx.session,
        business_id=ctx.user.business_id,
        actor_id=ctx.user.user_id,
        email=payload.email,
        full_name=payload.full_name,
        phone=payload.phone,
        role=payload.role,
        password=payload.password,
        store_id=str(payload.store_id) if payload.store_id else None,
        req=request,
    )
    return ok(result)


@router.get(
    "/employees",
    response_model=DataResponse[list[EmployeeListItem]],
    dependencies=[Depends(require_permission("employees:read"))],
)
async def employees(ctx: DbTenantDep):
    from sqlalchemy import text

    result = await ctx.session.execute(
        text("""
            SELECT u.id, u.email, u.full_name, u.phone, u.status,
                   u.last_login_at, u.created_at, u.store_id,
                   STRING_AGG(r.name, ',') AS role
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE u.tenant_id = :bid
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """),
        {"bid": ctx.user.business_id},
    )
    rows = result.mappings().all()
    return ok(
        [
            {
                # Keyed as the schema declares. It used to answer with
                # ``user_id``/``status``, which extra="ignore" dropped, so every
                # employee serialised with an empty id and is_active=True
                # regardless of the real status.
                "id": str(r["id"]),
                "email": r["email"],
                "full_name": r["full_name"],
                "phone": r["phone"],
                "role": r["role"] or "",
                "status": r["status"],
                "is_active": r["status"] == "active",
                "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "store_id": str(r["store_id"]) if r["store_id"] else None,
            }
            for r in rows
        ]
    )


@router.patch(
    "/employees/role",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:assign_roles"))],
)
async def update_role(payload: UpdateRoleRequest, ctx: TenantDep):
    await assign_role(
        ctx.user.business_id, str(payload.user_id), payload.new_role, actor_id=ctx.user.user_id
    )
    return ok(None, message="Role assigned")


@router.patch(
    "/employees/{user_id}",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:update"))],
)
async def update_employee(user_id: str, payload: UpdateEmployeeRequest, ctx: DbTenantDep):
    result = await auth_service.update_employee(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=user_id,
        full_name=payload.full_name,
        phone=payload.phone,
        email=payload.email,
        store_id=str(payload.store_id) if payload.store_id else None,
    )
    return ok(result, message="Employee updated")


@router.delete(
    "/employees/{user_id}",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:delete"))],
)
async def delete_employee(user_id: str, ctx: DbTenantDep):
    result = await auth_service.delete_employee(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=user_id,
    )
    return ok(result, message="Employee deleted")


@router.patch(
    "/employees/{user_id}/status",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:update"))],
)
async def set_employee_status(user_id: str, payload: EmployeeStatusRequest, ctx: DbTenantDep):
    result = await auth_service.set_employee_status(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=user_id,
        status=payload.status,
    )
    return ok(result, message=f"Employee {payload.status}")


@router.post(
    "/employees/{user_id}/force-signout",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_sessions"))],
)
async def force_signout(user_id: str, ctx: DbTenantDep):
    """Revoke every session an employee holds.

    Owners use this to sign someone out across the board — after a role
    change, a device that went missing, or a bad leave — without waiting for
    that device's refresh token to be next used.
    """
    result = await auth_service.get_employee(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=user_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Employee not found")
    from app.core.security import revoke_all_sessions
    from app.identity.models import AuthAuditLog
    from app.identity.repository import log_auth_event

    await revoke_all_sessions(user_id)
    await log_auth_event(
        ctx.session,
        AuthAuditLog(
            user_id=user_id,
            tenant_id=ctx.user.business_id,
            action="sessions_revoked",
            details=json.dumps({"by": ctx.user.user_id}),
        ),
    )
    return ok(result, message="Employee signed out")


@router.get(
    "/roles",
    response_model=DataResponse[list[RoleDetail]],
    dependencies=[Depends(require_permission("roles:read"))],
)
async def roles(ctx: TenantDep):
    return ok(await list_assignable_roles(tenant_id=ctx.user.business_id))


@router.get(
    "/roles/all",
    response_model=DataResponse[list[RoleDetail]],
    dependencies=[Depends(require_permission("roles:read"))],
)
async def all_roles(ctx: TenantDep):
    return ok(await list_all_roles(tenant_id=ctx.user.business_id))


@router.post(
    "/roles",
    status_code=201,
    response_model=DataResponse[RoleCreated],
    dependencies=[Depends(require_permission("roles:assign"))],
)
async def create_role(payload: RoleCreateRequest, ctx: TenantDep):
    try:
        result = await create_role_for_tenant(
            tenant_id=ctx.user.business_id,
            name=payload.name,
            rank=payload.rank,
            description=payload.description,
            permission_ids=payload.permission_ids,
        )
    except ValueError as exc:
        # Duplicate name, or a rank above the owner ceiling: the caller's fault.
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(result)


@router.patch(
    "/roles/{role_id}",
    response_model=DataResponse[RoleDetail],
    dependencies=[Depends(require_permission("roles:assign"))],
)
async def update_role_by_id(role_id: str, payload: RoleUpdateRequest, ctx: TenantDep):
    try:
        result = await update_role_for_tenant(
            tenant_id=ctx.user.business_id,
            role_id=role_id,
            name=payload.name,
            rank=payload.rank,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return ok(result)


@router.delete(
    "/roles/{role_id}",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("roles:assign"))],
)
async def delete_role(role_id: str, ctx: TenantDep):
    deleted = await delete_role_for_tenant(tenant_id=ctx.user.business_id, role_id=role_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Role not found")
    return ok(None, message="Role deleted")


@router.put(
    "/roles/{role_id}/permissions",
    response_model=DataResponse[RoleDetail],
    dependencies=[Depends(require_permission("roles:assign"))],
)
async def set_role_permissions(
    role_id: str,
    payload: RoleSetPermissionsRequest,
    ctx: DbTenantDep,
    request: Request,
):
    """Replace the permission set of a role.

    This rewrites what every holder of the role can do, so it needs a supervisor
    PIN unless the caller is an owner or higher. The change is audited with the
    before/after sets, and the affected users' cached permissions are dropped so
    nobody keeps access they just lost until the cache expires.
    """
    supervisor_id = await require_supervisor_approval(
        ctx,
        permission="roles:assign",
        supervisor_pin=payload.supervisor_pin,
        request=request,
    )
    result = await set_role_permissions_for_tenant(
        tenant_id=ctx.user.business_id,
        role_id=role_id,
        permission_ids=payload.permission_ids,
        actor_id=ctx.user.user_id,
        supervisor_id=supervisor_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return ok(result)


@router.get(
    "/permissions",
    response_model=DataResponse[list[PermissionItem]],
    dependencies=[Depends(require_permission("roles:read"))],
)
async def permissions():
    return ok(await list_all_permissions())


@router.get(
    "/employees/{user_id}/roles",
    response_model=DataResponse[list[RoleDetail]],
    dependencies=[Depends(require_permission("roles:read"))],
)
async def employee_roles(user_id: str, ctx: TenantDep):
    result = await get_user_roles(ctx.user.business_id, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    return ok(result)


@router.post(
    "/employees/{user_id}/roles",
    status_code=201,
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:assign_roles"))],
)
async def assign_employee_role(user_id: str, payload: UpdateRoleRequest, ctx: TenantDep):
    try:
        await assign_role(ctx.user.business_id, user_id, payload.new_role, actor_id=ctx.user.user_id)
    except ValueError as exc:
        # Not found / already assigned: the caller picking a role the user
        # already holds is a client state slip, not a server fault.
        raise HTTPException(status_code=409, detail=str(exc))
    return ok(None, message="Role assigned")


@router.delete(
    "/employees/{user_id}/roles/{role_name}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:assign_roles"))],
)
async def remove_employee_role(user_id: str, role_name: str, ctx: TenantDep):
    return ok(await remove_role(ctx.user.business_id, user_id, role_name))


@router.get(
    "/audit",
    response_model=DataResponse[list[AuditLogItem]],
    dependencies=[Depends(require_permission("employees:read"))],
)
async def audit(
    ctx: DbTenantDep,
    action: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    from uuid import UUID as _UUID

    from app.identity.repository import list_audit_logs

    logs = await list_audit_logs(
        ctx.session,
        _UUID(ctx.user.business_id),
        action=action,
        user_id=_UUID(user_id) if user_id else None,
        limit=limit,
        offset=offset,
    )
    return ok(
        [
            {
                "id": str(log.id),
                "user_id": str(log.user_id) if log.user_id else None,
                "action": log.action,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "details": json.loads(log.details) if isinstance(log.details, str) else log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    )


def _norm_audit_details(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


@router.get(
    "/activity",
    dependencies=[Depends(require_permission("employees:read"))],
)
async def tenant_activity(
    ctx: DbTenantDep,
    action: str | None = None,
    user_id: str | None = None,
    days: int = 90,
    page: int = 1,
    page_size: int = 50,
):
    """Unified activity trail for one tenant — mirrors the platform trail.

    Merges the two tenant-visible layers:
    - ``auth``: member authentication events (auth_audit_logs).
    - ``tenant``: the tenant's own business-action log (audit_events), which
      carries the resource that was acted on.

    Rows look like the platform audit trail so both consoles read alike.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select as sa_select

    from app.auth.audit import AuditEvent
    from app.identity.models import AuthAuditLog

    tid = UUID(ctx.user.business_id)
    now = datetime.now(timezone.utc)
    days = min(max(days, 1), 365)
    since = now - timedelta(days=days)

    rows: list[dict] = []

    auth_r = await ctx.session.execute(
        sa_select(
            AuthAuditLog.user_id,
            AuthAuditLog.action,
            AuthAuditLog.ip_address,
            AuthAuditLog.user_agent,
            AuthAuditLog.details,
            AuthAuditLog.created_at,
        )
        .where(
            AuthAuditLog.tenant_id == tid,
            AuthAuditLog.created_at >= since,
        )
        .order_by(AuthAuditLog.created_at.desc())
    )
    for r in auth_r.all():
        details = _norm_audit_details(r.details)
        approver = None
        if r.action == "cart_void_approved" and isinstance(details, dict):
            supervisor = details.get("supervisor_id")
            if supervisor:
                approver = str(supervisor)
        rows.append(
            {
                "at": r.created_at,
                "source": "auth",
                "actor": str(r.user_id) if r.user_id else None,
                "action": r.action,
                "detail": details,
                "status": "ok",
                "resource": None,
                "resource_id": None,
                "approver": approver,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
            }
        )

    biz_r = await ctx.session.execute(
        sa_select(
            AuditEvent.user_id,
            AuditEvent.action,
            AuditEvent.resource_type,
            AuditEvent.resource_id,
            AuditEvent.details,
            AuditEvent.status_code,
            AuditEvent.ip_address,
            AuditEvent.created_at,
        )
        .where(
            AuditEvent.business_id == str(tid),
            AuditEvent.created_at >= since,
        )
        .order_by(AuditEvent.created_at.desc())
    )
    for r in biz_r.all():
        rows.append(
            {
                "at": r.created_at,
                "source": "tenant",
                "actor": str(r.user_id) if r.user_id else None,
                "action": r.action,
                "detail": _norm_audit_details(r.details),
                "status": r.status_code,
                "resource": r.resource_type,
                "resource_id": r.resource_id,
                "approver": None,
                "ip_address": r.ip_address,
                "user_agent": None,
            }
        )

    if action:
        rows = [r for r in rows if r["action"] == action]
    if user_id:
        rows = [r for r in rows if r["actor"] == user_id or r["approver"] == user_id]

    rows.sort(key=lambda r: r["at"], reverse=True)

    total = len(rows)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    start = (page - 1) * page_size
    window = rows[start : start + page_size]
    for r in window:
        r["at"] = r["at"].isoformat() if r["at"] else None

    return ok(
        {
            "items": window,
            "total": total,
            "page": page,
            "page_size": page_size,
            "window_days": days,
        }
    )


@router.get(
    "/activity/actor/{actor_id}",
    dependencies=[Depends(require_permission("employees:read"))],
)
async def tenant_activity_actor(actor_id: str, ctx: DbTenantDep):
    """Resolve a tenant activity actor id into a human profile."""
    from sqlalchemy import select as sa_select

    from app.identity.models import Role, User, UserRole
    from app.stores.models import Store

    try:
        aid = UUID(actor_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Actor not found")

    tid = UUID(ctx.user.business_id)
    user = (
        await ctx.session.execute(
            sa_select(User).where(User.id == aid, User.tenant_id == tid)
        )
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Actor not found")

    store_name = None
    if user.store_id:
        store = (
            await ctx.session.execute(sa_select(Store).where(Store.id == user.store_id))
        ).scalar_one_or_none()
        store_name = store.name if store else None

    role_rows = (
        await ctx.session.execute(
            sa_select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == aid)
        )
    ).all()

    return ok(
        {
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "status": user.status,
            "roles": [r[0] for r in role_rows],
            "store_id": str(user.store_id) if user.store_id else None,
            "store_name": store_name,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }
    )
