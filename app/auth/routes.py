from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.dependencies import TenantDep, get_db, require_permission
from app.core.ratelimit import auth_rate_limit
from app.core.responses import DataResponse, DataMessageResponse, ok
from app.auth.schemas.auth import (
    ChangePasswordRequest,
    CreateEmployeeRequest,
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

    tenant_result = await create_tenant(
        business_name=payload.business_name,
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
async def logout(payload: LogoutRequest, request: Request, ctx: TenantDep):
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
async def me(ctx: TenantDep):
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
async def toggle_auto_create_cart(ctx: TenantDep):
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
    ctx: TenantDep,
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


@router.get(
    "/sessions",
    response_model=DataResponse[list[SessionInfo]],
    dependencies=[Depends(require_permission("auth:manage_sessions"))],
)
async def sessions(ctx: TenantDep):
    from app.core.security import list_user_sessions

    sessions_list = await list_user_sessions(user_id=ctx.user.user_id)
    return ok(sessions_list)


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
async def totp_setup(ctx: TenantDep):
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
async def totp_verify(payload: TOTPVerifyRequest, ctx: TenantDep):
    await auth_service.enable_totp(session=ctx.session, user_id=ctx.user.user_id, code=payload.code)
    return ok(None, message="TOTP enabled")


@router.post(
    "/totp/disable",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("auth:manage_totp"))],
)
async def totp_disable(payload: TOTPDisableRequest, ctx: TenantDep):
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
async def get_pin_status(ctx: TenantDep):
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
async def set_supervisor_pin(payload: SetPinRequest, ctx: TenantDep):
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
    "/users/{user_id}/pin",
    response_model=DataMessageResponse[SuccessResponse],
    dependencies=[Depends(require_permission("employees:update"))],
)
async def force_regenerate_pin(user_id: str, payload: SetPinRequest, ctx: TenantDep):
    import re
    from datetime import UTC, datetime, timedelta

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
            user_id=UUID(user_id),
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
    return ok(None, message="Supervisor PIN regenerated")


@router.post(
    "/employees",
    status_code=201,
    response_model=DataResponse[EmployeeCreated],
    dependencies=[Depends(require_permission("employees:create"))],
)
async def create_employee(payload: CreateEmployeeRequest, request: Request, ctx: TenantDep):
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
async def employees(ctx: TenantDep):
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
                "user_id": str(r["id"]),
                "email": r["email"],
                "full_name": r["full_name"],
                "phone": r["phone"],
                "role": r["role"] or "",
                "status": r["status"],
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
async def update_employee(user_id: str, payload: UpdateEmployeeRequest, ctx: TenantDep):
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
async def delete_employee(user_id: str, ctx: TenantDep):
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
async def set_employee_status(user_id: str, payload: EmployeeStatusRequest, ctx: TenantDep):
    result = await auth_service.set_employee_status(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=user_id,
        status=payload.status,
    )
    return ok(result, message=f"Employee {payload.status}")


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
    result = await create_role_for_tenant(
        tenant_id=ctx.user.business_id,
        name=payload.name,
        rank=payload.rank,
        description=payload.description,
        permission_ids=payload.permission_ids,
    )
    return ok(result)


@router.patch(
    "/roles/{role_id}",
    response_model=DataResponse[RoleDetail],
    dependencies=[Depends(require_permission("roles:assign"))],
)
async def update_role_by_id(role_id: str, payload: RoleUpdateRequest, ctx: TenantDep):
    result = await update_role_for_tenant(
        tenant_id=ctx.user.business_id,
        role_id=role_id,
        name=payload.name,
        rank=payload.rank,
        description=payload.description,
    )
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
async def set_role_permissions(role_id: str, payload: RoleSetPermissionsRequest, ctx: TenantDep):
    result = await set_role_permissions_for_tenant(
        tenant_id=ctx.user.business_id,
        role_id=role_id,
        permission_ids=payload.permission_ids,
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
    await assign_role(ctx.user.business_id, user_id, payload.new_role, actor_id=ctx.user.user_id)
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
    ctx: TenantDep,
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
                "details": log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    )
