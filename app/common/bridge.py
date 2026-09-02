"""Service bridge — connects the API layer to new service packages.

Each method uses the single shared database with schema-per-domain
and delegates to the service package's planning functions + repository layer.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.common.cache import cache, cached

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.repository import get_account_by_id
from app.common import flutterwave_service, logging
from app.core.config import settings
from app.common.db.engine import ServiceDatabase, create_database
from app.common.settings import get_common_settings
from app.common.utils import generate_random_timestamp_string


common_settings = get_common_settings()

_databases: dict[str, ServiceDatabase] = {}
_shared_engine = None


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _get_shared_engine():
    """Return a single shared SQLAlchemy engine for all service schemas."""
    global _shared_engine
    if _shared_engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        _shared_engine = create_async_engine(
            common_settings.database_url,
            pool_size=30,
            max_overflow=15,
            pool_pre_ping=True,
            pool_timeout=60,
            echo=False,
            future=True,
            connect_args={"command_timeout": 60},
        )
    return _shared_engine


def _get_sdb(service: str) -> ServiceDatabase:
    """Return a cached ServiceDatabase for the given service schema.

    All services share a single connection pool. The schema is set via
    ``SET search_path`` on each session checkout.
    """
    if service not in _databases:
        engine = _get_shared_engine()
        from sqlalchemy.ext.asyncio import async_sessionmaker
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        _databases[service] = ServiceDatabase(engine=engine, session_factory=factory, schema=service)
    return _databases[service]


async def _record_movement(
    session,
    *,
    tenant_id: UUID,
    product_id: UUID,
    store_id: UUID,
    movement_type: str,
    qty_change: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    reason: str | None = None,
    unit_cost: Decimal | None = None,
    notes: str | None = None,
    created_by: UUID | None = None,
) -> None:
    """Insert a stock_movement record for full audit trail."""
    from app.inventory.models import StockMovement

    movement = StockMovement(
        tenant_id=tenant_id,
        product_id=product_id,
        store_id=store_id,
        movement_type=movement_type,
        qty_change=qty_change,
        balance_before=balance_before,
        balance_after=balance_after,
        reference_type=reference_type,
        reference_id=reference_id,
        reason=reason,
        unit_cost=unit_cost,
        notes=notes,
        created_by=created_by,
    )
    session.add(movement)


# ═══════════════════════════════════════════════════════════════════════════════
#  TENANT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_tenant(
    business_name: str,
    business_email: str,
    owner_name: str,
    owner_email: str,
    owner_phone: str | None,
    owner_password_hash: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a new tenant along with its owner and default role configuration.

    Performs the complete tenant onboarding flow: generates a unique URL slug
    from the business name, plans the tenant and user models via the tenancy
    service, persists them, creates the owner role with full permissions, and
    seeds default roles (manager, cashier, inventory, viewer) with
    role-specific permission sets.

    Args:
        business_name: Display name of the business or organisation.
        business_email: Primary contact email for the business.
        owner_name: Full name of the tenant owner.
        owner_email: Email address of the tenant owner.
        owner_phone: Optional phone number of the tenant owner.
        owner_password_hash: Hashed password for the owner account.
        actor_id: Optional identifier of the user performing this action
            (used for audit logging).
        correlation_id: Optional correlation identifier for distributed
            tracing across services.

    Returns:
        A dictionary containing the newly created tenant details including
        ``id``, ``slug``, ``business_name``, ``tier``, and ``status``.

    Raises:
        ValueError: If the generated slug already exists and cannot be
            made unique within the retry limit.
    """
    from app.tenancy.schemas import TenantCreateCommand
    from app.tenancy.service import plan_tenant_creation, slugify
    from app.tenancy.repository import create_tenant as repo_create_tenant, slug_exists
    from app.identity.models import Permission
    from app.identity.repository import (
        create_user as repo_create_user,
        get_role_by_name_for_tenant,
        assign_role_to_user,
        get_all_permissions,
        set_role_permissions,
    )

    base_slug = slugify(business_name)
    slug = base_slug
    sdb = _get_sdb("tenancy")
    async with sdb.session() as session:
        counter = 1
        while await slug_exists(session, slug):
            slug = f"{base_slug}-{counter}"
            counter += 1

    command = TenantCreateCommand(
        actor_id=UUID(actor_id) if actor_id else None,
        business_name=business_name,
        business_email=business_email,
        owner_name=owner_name,
        owner_email=owner_email,
        owner_phone=owner_phone,
        owner_password_hash=owner_password_hash,
        tier="starter",
        correlation_id=correlation_id,
    )

    result, tenant_model, user_model, outbox = plan_tenant_creation(command, slug)

    async with sdb.session() as session:
        await repo_create_tenant(session, tenant_model)
        await repo_create_user(session, user_model)

        # Create the "owner" role for this tenant (if missing)
        tenant_uid = tenant_model.id
        role = await get_role_by_name_for_tenant(session, tenant_uid, "owner")
        if not role:
            from app.identity.models import Role

            role = Role(
                name="owner",
                rank=1,
                description="Full access to all features",
                tenant_id=tenant_uid,
            )
            session.add(role)
            await session.flush()
            # Assign all permissions to owner
            from app.identity.repository import get_all_permissions, set_role_permissions

            all_perms = await get_all_permissions(session)
            await set_role_permissions(session, role.id, [p.id for p in all_perms])
        await assign_role_to_user(session, user_model.id, role.id, assigned_by=user_model.id)

        # Seed default roles for the tenant
        DEFAULT_ROLE_DEFS = [
            ("manager", 60, "Day-to-day operations"),
            ("cashier", 40, "Process sales at registers"),
            ("inventory", 50, "Manage product stock"),
            ("viewer", 20, "Read-only access"),
        ]

        ROLE_PERMISSIONS_MAP = {
            "manager": [
                "auth:login",
                "auth:manage_totp",
                "auth:manage_sessions",
                "users:create",
                "users:read",
                "users:update",
                "roles:read",
                "products:create",
                "products:read",
                "products:update",
                "products:delete",
                "categories:create",
                "categories:read",
                "categories:update",
                "categories:delete",
                "inventory:read",
                "inventory:write",
                "inventory:adjust",
                "inventory:transfer",
                "warehouses:create",
                "warehouses:read",
                "warehouses:update",
                "sales:create",
                "sales:read",
                "sales:void",
                "documents:create",
                "documents:read",
                "documents:update",
                "documents:manage",
                "payments:create",
                "payments:read",
                "payments:confirm",
                "terminals:create",
                "terminals:read",
                "terminals:update",
                "employees:create",
                "employees:read",
                "employees:update",
                "accounting:read",
                "accounting:write",
                "accounting:post",
                "reports:read",
                "ai:generate",
                "ai:read",
                "ai:manage",
                "cart:create",
                "cart:read",
                "cart:checkout",
                "sync:read",
                "sync:manage",
            ],
            "cashier": [
                "auth:login",
                "auth:manage_totp",
                "auth:manage_sessions",
                "users:read",
                "products:read",
                "categories:read",
                "inventory:read",
                "warehouses:read",
                "sales:create",
                "sales:read",
                "documents:read",
                "payments:create",
                "payments:read",
                "terminals:read",
                "reports:read",
                "ai:read",
                "cart:create",
                "cart:read",
                "cart:checkout",
            ],
            "inventory": [
                "auth:login",
                "auth:manage_totp",
                "auth:manage_sessions",
                "users:read",
                "products:create",
                "products:read",
                "products:update",
                "products:delete",
                "categories:create",
                "categories:read",
                "categories:update",
                "categories:delete",
                "inventory:read",
                "inventory:write",
                "inventory:adjust",
                "inventory:transfer",
                "warehouses:create",
                "warehouses:read",
                "warehouses:update",
                "reports:read",
            ],
            "viewer": [
                "auth:login",
                "auth:manage_totp",
                "auth:manage_sessions",
                "users:read",
                "roles:read",
                "products:read",
                "categories:read",
                "inventory:read",
                "warehouses:read",
                "sales:read",
                "documents:read",
                "payments:read",
                "terminals:read",
                "employees:read",
                "accounting:read",
                "reports:read",
                "ai:read",
                "cart:read",
                "sync:read",
            ],
        }

        for role_name, rank, desc in DEFAULT_ROLE_DEFS:
            existing_role = await get_role_by_name_for_tenant(session, tenant_uid, role_name)
            if existing_role:
                continue
            new_role = Role(
                tenant_id=tenant_uid,
                name=role_name,
                rank=rank,
                description=desc,
            )
            session.add(new_role)
            await session.flush()
            perm_names = ROLE_PERMISSIONS_MAP.get(role_name, [])
            for perm_name in perm_names:
                result_perms = await session.execute(
                    select(Permission).where(Permission.name == perm_name)
                )
                perm = result_perms.scalar_one_or_none()
                if perm:
                    from app.identity.models import RolePermission

                    session.add(RolePermission(role_id=new_role.id, permission_id=perm.id))

        for write in outbox:
            session.add(write.to_model())

        # Seed default Chart of Accounts for the new tenant
        from app.accounting.seed import seed_chart_of_accounts

        await seed_chart_of_accounts(tenant_uid, session)

        await session.commit()

    return result.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════════
#  USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_user(
    tenant_id: str,
    email: str,
    full_name: str,
    password_hash: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a new user account within a tenant.

    Plans the user creation through the identity service, persists the user
    model to the identity database, and records outbox events for downstream
    notification.

    Args:
        tenant_id: Unique identifier of the tenant that owns this user.
        email: Email address for the new user account.
        full_name: Full display name of the user.
        password_hash: Hashed password for authentication.
        actor_id: Optional identifier of the user performing this action.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created user details including
        ``id``, ``email``, ``full_name``, and ``status``.
    """
    from app.identity.schemas import UserCreateCommand
    from app.identity.service import plan_user_creation
    from app.identity.repository import create_user as repo_create_user

    command = UserCreateCommand(
        tenant_id=UUID(tenant_id),
        actor_id=UUID(actor_id) if actor_id else None,
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        correlation_id=correlation_id,
    )

    result, user_model, outbox = plan_user_creation(command)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        await repo_create_user(session, user_model)
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return result.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════════
#  ROLE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


@cached(prefix="roles:list", ttl=3600, key_func=lambda tenant_id=None, **kw: str(tenant_id or "all"))
async def list_assignable_roles(tenant_id: str | None = None) -> list[dict]:
    """List all roles that can be assigned to users, optionally filtered by tenant.

    Retrieves assignable roles from the identity database and enriches each
    role with its associated permission names. When a tenant identifier is
    provided, only roles belonging to that tenant are returned.

    Args:
        tenant_id: Optional tenant identifier to filter roles. When ``None``,
            all assignable roles are returned regardless of tenant.

    Returns:
        A list of dictionaries, each containing ``id``, ``name``, ``rank``,
        ``description``, and a ``permissions`` list of permission name strings.
    """
    from app.identity.repository import get_assignable_roles, get_permissions_for_role

    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        roles = await get_assignable_roles(session)
        result = []
        for r in roles:
            if tenant_id and str(r.tenant_id) != tenant_id:
                continue
            perms = await get_permissions_for_role(session, r.id)
            result.append(
                {
                    "id": str(r.id),
                    "name": r.name,
                    "rank": r.rank,
                    "description": r.description,
                    "permissions": [p.name for p in perms],
                }
            )
        return result


@cached(prefix="roles:all", ttl=3600, key_func=lambda tenant_id=None, **kw: str(tenant_id or "all"))
async def list_all_roles(tenant_id: str | None = None) -> list[dict]:
    """List all roles (assignable and system) for a tenant.

    Returns every role from the identity database with its associated
    permission names. Supports optional filtering by tenant.

    Args:
        tenant_id: Optional tenant identifier to filter roles. When ``None``,
            all roles are returned.

    Returns:
        A list of dictionaries, each containing ``id``, ``name``, ``rank``,
        ``description``, and a ``permissions`` list of permission name strings.
    """
    from app.identity.repository import get_all_roles, get_permissions_for_role

    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        roles = await get_all_roles(session)
        result = []
        for r in roles:
            if tenant_id and str(r.tenant_id) != tenant_id:
                continue
            perms = await get_permissions_for_role(session, r.id)
            result.append(
                {
                    "id": str(r.id),
                    "name": r.name,
                    "rank": r.rank,
                    "description": r.description,
                    "permissions": [p.name for p in perms],
                }
            )
        return result


@cached(prefix="permissions:all", ttl=86400, key_func=lambda: "all")
async def list_all_permissions() -> list[dict]:
    """List all available permissions in the identity system.

    Retrieves every registered permission from the identity database and
    returns them as a flat list of dictionaries.

    Returns:
        A list of dictionaries, each containing ``id``, ``name``, and
        ``description`` of a permission.
    """
    from app.identity.repository import get_all_permissions

    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        perms = await get_all_permissions(session)
        return [{"id": str(p.id), "name": p.name, "description": p.description} for p in perms]


async def get_user_roles(tenant_id: str, user_id: str) -> list[dict] | None:
    """Retrieve the roles currently assigned to a specific user.

    Validates that the user belongs to the specified tenant before returning
    their assigned roles.

    Args:
        tenant_id: Unique identifier of the tenant the user belongs to.
        user_id: Unique identifier of the user whose roles are being queried.

    Returns:
        A list of role dictionaries each containing ``id``, ``name``, ``rank``,
        and ``description``, or ``None`` if the user does not exist or belongs
        to a different tenant.
    """
    from app.identity.repository import get_user_by_id, get_user_roles

    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        user = await get_user_by_id(session, UUID(user_id))
        if not user or str(user.tenant_id) != tenant_id:
            return None
        roles = await get_user_roles(session, UUID(user_id))
        return [
            {"id": str(r.id), "name": r.name, "rank": r.rank, "description": r.description}
            for r in roles
        ]


async def assign_role(
    tenant_id: str, user_id: str, role_name: str, actor_id: str | None = None
) -> dict:
    """Assign a role to a user within a tenant.

    Validates that the user and role both exist and belong to the specified
    tenant, ensures the role is not already assigned, then persists the
    assignment and records outbox events.

    Args:
        tenant_id: Unique identifier of the tenant.
        user_id: Unique identifier of the user to receive the role.
        role_name: Name of the role to assign (e.g. "manager", "cashier").
        actor_id: Optional identifier of the user performing this action.

    Returns:
        A dictionary containing ``user_id`` and ``role`` confirming the
        assignment.

    Raises:
        ValueError: If the user is not found, the role does not exist within
            the tenant, or the role is already assigned to the user.
    """
    from app.identity.repository import (
        assign_role_to_user,
        get_role_by_name_for_tenant,
        get_user_by_id,
        get_user_roles,
    )
    from app.identity.service import plan_role_assignment
    from app.identity.schemas import UserRoleAssignCommand

    tenant_uid = UUID(tenant_id)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        user = await get_user_by_id(session, UUID(user_id))
        if not user or str(user.tenant_id) != tenant_id:
            raise ValueError("user_not_found")

        role = await get_role_by_name_for_tenant(session, tenant_uid, role_name)
        if not role:
            raise ValueError(f"role_not_found:{role_name}")

        existing = await get_user_roles(session, UUID(user_id))
        if any(r.id == role.id for r in existing):
            raise ValueError("role_already_assigned")

        command = UserRoleAssignCommand(
            user_id=UUID(user_id),
            role_name=role_name,
            actor_id=UUID(actor_id) if actor_id else None,
        )

        outbox = plan_role_assignment(
            tenant_id=tenant_uid,
            user_id=UUID(user_id),
            role_name=role_name,
            actor_id=UUID(actor_id) if actor_id else None,
            correlation_id=command.correlation_id,
        )
        await assign_role_to_user(
            session, UUID(user_id), role.id, assigned_by=UUID(actor_id) if actor_id else None
        )
        for write in outbox:
            session.add(write.to_model())
        await session.commit()
        await cache.delete_pattern(f"sf:cache:roles:*:{tenant_id}*")

        return {"user_id": user_id, "role": role_name}


async def remove_role(tenant_id: str, user_id: str, role_name: str) -> dict:
    """Remove a role assignment from a user within a tenant.

    Validates that the user and role both exist and belong to the specified
    tenant, then removes the role assignment.

    Args:
        tenant_id: Unique identifier of the tenant.
        user_id: Unique identifier of the user whose role is being removed.
        role_name: Name of the role to remove (e.g. "manager", "cashier").

    Returns:
        A dictionary containing ``user_id``, ``role``, and ``removed: True``
        confirming the removal.

    Raises:
        ValueError: If the user is not found or the role does not exist
            within the tenant.
    """
    from app.identity.repository import (
        get_role_by_name_for_tenant,
        get_user_by_id,
        remove_role_from_user,
    )

    tenant_uid = UUID(tenant_id)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        user = await get_user_by_id(session, UUID(user_id))
        if not user or str(user.tenant_id) != tenant_id:
            raise ValueError("user_not_found")

        role = await get_role_by_name_for_tenant(session, tenant_uid, role_name)
        if not role:
            raise ValueError(f"role_not_found:{role_name}")

        await remove_role_from_user(session, UUID(user_id), role.id)
        await session.commit()
        await cache.delete_pattern(f"sf:cache:roles:*:{tenant_id}*")

        return {"user_id": user_id, "role": role_name, "removed": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  PRODUCT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_product(
    tenant_id: str,
    tenant_slug: str,
    name: str,
    selling_price: Decimal,
    sku: str | None = None,
    category_id: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    store_id: str | None = None,
) -> dict:
    """Create a single product within a tenant.

    Convenience wrapper around ``create_products`` that creates one product
    and returns its details directly.

    Args:
        tenant_id: Unique identifier of the tenant that owns the product.
        tenant_slug: URL-friendly slug of the tenant, used in QR payloads.
        name: Display name of the product.
        selling_price: Price at which the product is sold.
        sku: Optional stock keeping unit identifier.
        category_id: Optional category to which the product belongs.
        actor_id: Optional identifier of the user performing this action.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created product details including
        ``id``, ``public_id``, ``name``, ``selling_price``, ``qr_payload``,
        and ``qr_url``.
    """
    result = await create_products(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        items=[
            {
                "name": name,
                "selling_price": float(selling_price),
                "sku": sku,
                "category_id": category_id,
            }
        ],
        actor_id=actor_id,
        correlation_id=correlation_id,
        store_id=store_id,
    )
    return result["products"][0]


async def create_products(
    tenant_id: str,
    tenant_slug: str,
    items: list[dict],
    actor_id: str | None = None,
    correlation_id: str | None = None,
    store_id: str | None = None,
) -> dict:
    """Create multiple products in a single batch operation.

    Plans and persists each product through the catalog service. Products
    that fail validation are captured as errors without aborting the entire
    batch. After persistence, a background task is scheduled to generate QR
    codes for each newly created product.

    Args:
        tenant_id: Unique identifier of the tenant that owns the products.
        tenant_slug: URL-friendly slug of the tenant, used in QR payloads.
        items: List of product dictionaries, each containing ``name``,
            ``selling_price``, and optionally ``sku`` and ``category_id``.
        actor_id: Optional identifier of the user performing this action.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``products`` (list of created product detail
        dictionaries) and ``errors`` (list of failed items with their error
        messages).
    """
    from app.catalog.models import Product
    from app.catalog.repository import update_product
    from app.catalog.schemas import ProductCreateCommand
    from app.catalog.service import plan_product_creation

    products: list[ProductCreateCommand] = []
    errors: list[dict] = []
    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        for item in items:
            try:
                name = item["name"]
                selling_price = Decimal(str(item["selling_price"]))
                sku = item.get("sku")
                category_id = item.get("category_id")

                command = ProductCreateCommand(
                    tenant_id=UUID(tenant_id),
                    actor_id=UUID(actor_id) if actor_id else None,
                    tenant_slug=tenant_slug,
                    name=name,
                    sku=sku,
                    category_id=UUID(category_id) if category_id else None,
                    selling_price=selling_price,
                    correlation_id=correlation_id,
                )

                result, outbox = plan_product_creation(command)
                product = Product(
                    id=result.product_id,
                    tenant_id=UUID(tenant_id),
                    public_id=result.public_id,
                    name=name,
                    sku=sku,
                    category_id=UUID(category_id) if category_id else None,
                    selling_price=float(selling_price),
                    qr_payload=result.qr_payload,
                    qr_url=result.qr_url,
                )
                session.add(product)
                for write in outbox:
                    session.add(write.to_model())
                products.append(result)
            except Exception as exc:
                errors.append({"item": item, "error": str(exc)})

        await session.commit()

    # Generate QR codes (URL-based) for each product and upload to Cloudinary
    from app.catalog.cloudinary_upload import upload_qr_png
    from app.catalog.qr import build_product_qr_url, generate_qr_png

    api_base = settings.api_base_url

    for result in products:
        try:
            qr_url = build_product_qr_url(
                base_url=api_base,
                store_id=store_id or "",
                product_id=str(result.product_id),
            )
            png_bytes = generate_qr_png(qr_url, box_size=20, border=4)
            upload_result = upload_qr_png(
                tenant_id=tenant_id,
                product_id=str(result.product_id),
                png_bytes=png_bytes,
            )
            if upload_result["url"]:
                sdb = _get_sdb("catalog")
                async with sdb.session() as session:
                    await update_product(
                        session,
                        UUID(str(result.product_id)),
                        qr_url=upload_result["url"],
                        qr_asset_id=upload_result["public_id"],
                    )
                    await session.commit()
                result.qr_url = upload_result["url"]
        except Exception:
            pass

    # Create store_products records if store_id is provided
    if store_id:
        from app.stores.models import StoreProduct
        from app.stores.sku import generate_unique_sku

        sdb_stores = _get_sdb("inventory")
        async with sdb_stores.session() as store_session:
            for result in products:
                try:
                    auto_sku = await generate_unique_sku(store_session, UUID(tenant_id))
                    store_product = StoreProduct(
                        tenant_id=UUID(tenant_id),
                        store_id=UUID(store_id),
                        product_id=result.product_id,
                        name=result.name,
                        sku=auto_sku,
                        selling_price=float(result.selling_price),
                        cost_price=0,
                        tax_rate=None,
                        reorder_point=0,
                        image_url=None,
                        status="active",
                    )
                    store_session.add(store_product)
                except Exception:
                    pass
            await store_session.commit()

    return {
        "products": [r.model_dump(mode="json") for r in products],
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  CART MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_cart(
    tenant_id: str,
    # session_id: str,
    store_id: str,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a new shopping cart for a checkout session.

    Plans and persists a new cart model through the cart service. The cart
    is scoped to a session identifier.

    Args:
        tenant_id: Unique identifier of the tenant that owns the cart.
        session_id: Unique session identifier linking the cart to a
            client-side checkout session.
        customer_name: Optional customer name for the cart.
        customer_phone: Optional customer phone number for the cart.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created cart details including
        ``id``, ``session_id``, ``status``, and ``expires_at``.
    """
    from app.cart.schemas import CartCreateCommand
    from app.cart.service import plan_cart_creation
    from app.cart.repository import create_cart as repo_create_cart

    command = CartCreateCommand(
        store_id=UUID(store_id),
        customer_name=customer_name,
        customer_phone=customer_phone,
    )

    result, cart_model, outbox = plan_cart_creation(
        command,
        tenant_id=UUID(tenant_id),
        # actor_id=UUID(actor_id) if actor_id else None,
        correlation_id=correlation_id,
    )
    sdb = _get_sdb("cart")
    async with sdb.session() as session:
        await repo_create_cart(session, cart_model)
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return result.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════════
#  SALE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_sale_via_service(
    tenant_id: str,
    cashier_id: str,
    items: list[dict],
    discount: Decimal | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    store_id: str | None = None,
    notes: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a sale record through the sales service.

    Plans and persists a new sale with its line items via the sales service.
    This is the primary entry point for recording completed transactions.

    Args:
        tenant_id: Unique identifier of the tenant that owns the sale.
        cashier_id: Identifier of the cashier or user processing the sale.
        items: List of sale item dictionaries, each containing
            ``product_id``, ``product_name``, ``qty``, ``unit_price``, and
            optionally ``discount_pct`` and ``tax_rate``.
        discount: Optional discount applied to the entire sale.
        customer_name: Optional name of the customer.
        customer_phone: Optional phone number of the customer.
        store_id: Optional identifier of the store where the sale occurred.
        notes: Optional notes attached to the sale.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created sale details including
        ``id``, ``sale_number``, ``total``, ``amount_paid``, and ``status``.
    """
    from app.sales.schemas import SaleCreateCommand, SaleItemLine
    from app.sales.service import plan_sale_creation
    from app.sales.repository import create_sale as repo_create_sale, create_sale_items

    sale_items = [
        SaleItemLine(
            product_id=UUID(i["product_id"]),
            product_name=i["product_name"],
            qty=Decimal(str(i["qty"])),
            unit_price=Decimal(str(i["unit_price"])),
            discount_pct=Decimal(str(i.get("discount_pct", 0))),
            tax_rate=Decimal(str(i["tax_rate"])) if i.get("tax_rate") else None,
        )
        for i in items
    ]

    command = SaleCreateCommand(
        tenant_id=UUID(tenant_id),
        cashier_id=UUID(cashier_id),
        store_id=UUID(store_id) if store_id else None,
        customer_name=customer_name,
        customer_phone=customer_phone,
        items=sale_items,
        discount=discount or Decimal("0"),
        notes=notes,
        correlation_id=correlation_id,
    )

    result, sale_model, sale_item_models, outbox = plan_sale_creation(command)
    sdb = _get_sdb("sales")
    async with sdb.session() as session:
        await repo_create_sale(session, sale_model)
        await create_sale_items(session, sale_item_models)
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return result.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTING
# ═══════════════════════════════════════════════════════════════════════════════


async def create_account(
    tenant_id: str,
    code: str,
    name: str,
    account_type: str,
    parent_id: str | None = None,
) -> dict:
    """Create a new account in the chart of accounts.

    Plans and persists a new accounting account through the accounting
    service. Accounts can be hierarchically linked via a parent account.

    Args:
        tenant_id: Unique identifier of the tenant that owns the account.
        code: Unique code for the account (e.g. "1001" for cash).
        name: Display name of the account.
        account_type: Type of account (e.g. "asset", "liability",
            "equity", "revenue", "expense").
        parent_id: Optional identifier of the parent account for
            hierarchical organisation.

    Returns:
        A dictionary containing the newly created account details including
        ``id``, ``code``, ``name``, ``account_type``, and ``parent_id``.
    """
    from app.accounting.schemas import ChartOfAccountCreateCommand
    from app.accounting.service import plan_create_account
    from app.accounting.repository import create_account as repo_create_account

    command = ChartOfAccountCreateCommand(
        tenant_id=UUID(tenant_id),
        code=code,
        name=name,
        account_type=account_type,
        parent_id=UUID(parent_id) if parent_id else None,
    )

    result, account_model = plan_create_account(command)
    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        await repo_create_account(session, account_model)
        await session.commit()

    return result.model_dump(mode="json")


async def list_accounts(tenant_id: str) -> list[dict]:
    """List all accounts in the Chart of Accounts for a tenant.

    Retrieves all financial accounts (assets, liabilities, equity, revenue,
    expenses) for the specified business tenant, ordered by account code.

    Args:
        tenant_id: Unique identifier of the tenant whose accounts to list.

    Returns:
        A list of dictionaries, each containing account details: id, code,
        name, account_type, status.
    """
    from app.accounting.repository import list_accounts as repo_list_accounts

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        accounts = await repo_list_accounts(session, UUID(tenant_id))
        return [
            {
                "id": str(a.id),
                "code": a.code,
                "name": a.name,
                "account_type": a.account_type,
                "status": a.status,
            }
            for a in accounts
        ]


async def get_balance_sheet(
    tenant_id: str,
    as_at_date: datetime | None = None,
) -> dict:
    """Get the Balance Sheet as of a specific date.

    The Balance Sheet shows the business's financial position:
        Assets = Liabilities + Equity

    Args:
        tenant_id: Unique identifier of the tenant.
        as_at_date: The date to calculate the balance sheet for. If None,
            uses the current date.

    Returns:
        A dictionary containing assets, liabilities, equity totals and details.
    """
    from app.accounting.repository import get_balance_sheet as repo_get_balance_sheet

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        return await repo_get_balance_sheet(session, UUID(tenant_id), as_at_date)


async def get_cash_flow(
    tenant_id: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict:
    """Get the Cash Flow Statement for a date range.

    Shows cash inflows and outflows from operating activities.

    Args:
        tenant_id: Unique identifier of the tenant.
        from_date: Start of the period. If None, defaults to 30 days ago.
        to_date: End of the period. If None, defaults to today.

    Returns:
        A dictionary containing inflows, outflows, and net cash flow.
    """
    from app.accounting.repository import get_cash_flow as repo_get_cash_flow

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        return await repo_get_cash_flow(session, UUID(tenant_id), from_date, to_date)


async def list_accounts_receivable(
    tenant_id: str,
    status_filter: str | None = None,
) -> list[dict]:
    """List Accounts Receivable (who owes us money).

    Args:
        tenant_id: Unique identifier of the tenant.
        status_filter: Optional status to filter by (pending, overdue, partial, paid).

    Returns:
        A list of dictionaries containing AR record details.
    """
    from app.accounting.repository import (
        list_accounts_receivable as repo_list_ar,
    )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        ar_list = await repo_list_ar(session, UUID(tenant_id), status_filter)
        return [
            {
                "id": str(ar.id),
                "customer_id": str(ar.customer_id),
                "customer_name": ar.customer_name,
                "invoice_number": ar.invoice_number,
                "amount": float(ar.amount),
                "amount_paid": float(ar.amount_paid),
                "balance": float(ar.balance),
                "due_date": ar.due_date.isoformat() if ar.due_date else None,
                "status": ar.status,
            }
            for ar in ar_list
        ]


async def create_accounts_receivable(
    tenant_id: str,
    customer_id: str,
    customer_name: str,
    invoice_number: str,
    amount: float,
    due_date: str,
    invoice_id: str | None = None,
) -> dict:
    """Create a new Accounts Receivable record.

    Tracks money owed by a customer for goods or services delivered.

    Args:
        tenant_id: Unique identifier of the tenant.
        customer_id: UUID of the customer who owes the money.
        customer_name: Name of the customer (denormalized for display).
        invoice_number: The invoice number (e.g., "INV-20260001").
        amount: Total invoice amount in NGN.
        due_date: Date by which payment is expected (ISO format).
        invoice_id: Optional UUID of the linked Document (invoice).

    Returns:
        A dictionary containing the newly created AR record details.
    """
    from app.accounting.schemas import AccountsReceivableCreateCommand
    from app.accounting.service import plan_create_accounts_receivable
    from app.accounting.repository import (
        create_accounts_receivable as repo_create_ar,
    )

    command = AccountsReceivableCreateCommand(
        tenant_id=UUID(tenant_id),
        invoice_id=UUID(invoice_id) if invoice_id else None,
        customer_id=UUID(customer_id),
        customer_name=customer_name,
        invoice_number=invoice_number,
        amount=amount,
        due_date=datetime.fromisoformat(due_date),
    )

    result, ar_model = plan_create_accounts_receivable(command)
    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        await repo_create_ar(session, ar_model)
        await session.commit()

    return result.model_dump(mode="json")


async def record_ar_payment(
    tenant_id: str,
    ar_id: str,
    amount: float,
    payment_date: str,
    notes: str | None = None,
) -> dict:
    """Record a payment against an Accounts Receivable record.

    When a customer pays their invoice:
    1. Updates the AR record (amount_paid, balance, status)
    2. Creates journal entries (Debit Cash, Credit AR)

    Args:
        tenant_id: Unique identifier of the tenant.
        ar_id: UUID of the AR record being paid.
        amount: Payment amount in NGN.
        payment_date: Date the payment was made (ISO format).
        notes: Optional notes about the payment.

    Returns:
        A dictionary containing the updated AR record details.
    """
    from app.accounting.repository import (
        get_accounts_receivable_by_id,
        update_ar_payment,
        create_journal,
        create_journal_entry,
    )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        # Get the AR record
        ar = await get_accounts_receivable_by_id(session, UUID(ar_id))
        if not ar:
            raise ValueError(f"Accounts Receivable record not found: {ar_id}")

        # Update the AR record
        updated_ar = await update_ar_payment(session, UUID(ar_id), amount)

        # Create journal entry: Debit Cash, Credit AR
        from app.accounting.service import plan_record_ar_payment
        from app.accounting.schemas import PaymentRecordCommand
        from uuid import uuid4

        command = PaymentRecordCommand(
            tenant_id=UUID(tenant_id),
            amount=amount,
            payment_date=datetime.fromisoformat(payment_date),
            notes=notes,
        )
        ar_result, journal, entries, events = plan_record_ar_payment(
            command, UUID(ar_id), UUID(tenant_id)
        )

        # Resolve account IDs
        from app.accounting.repository import get_account_by_code

        cash_account = await get_account_by_code(session, UUID(tenant_id), "1000")
        ar_account = await get_account_by_code(session, UUID(tenant_id), "1100")

        if cash_account and ar_account:
            # Update entries with correct account IDs
            for entry in entries:
                if entry.account_code == "1000":
                    entry.account_id = cash_account.id
                elif entry.account_code == "1100":
                    entry.account_id = ar_account.id

            # Create journal and entries
            await create_journal(session, journal)
            for entry in entries:
                entry.status = "posted"
                entry.posted_at = journal.posted_at
                await create_journal_entry(session, entry)

            # Post the journal
            from app.accounting.repository import post_journal as repo_post_journal

            await repo_post_journal(session, journal.id, None)
            updated_ar.journal_id = journal.id

        await session.commit()

    return {
        "id": str(updated_ar.id),
        "amount_paid": float(updated_ar.amount_paid),
        "balance": float(updated_ar.balance),
        "status": updated_ar.status,
    }


async def list_accounts_payable(
    tenant_id: str,
    status_filter: str | None = None,
) -> list[dict]:
    """List Accounts Payable (who we owe money to).

    Args:
        tenant_id: Unique identifier of the tenant.
        status_filter: Optional status to filter by (pending, overdue, partial, paid).

    Returns:
        A list of dictionaries containing AP record details.
    """
    from app.accounting.repository import (
        list_accounts_payable as repo_list_ap,
    )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        ap_list = await repo_list_ap(session, UUID(tenant_id), status_filter)
        return [
            {
                "id": str(ap.id),
                "bill_number": ap.bill_number,
                "vendor_name": ap.vendor_name,
                "description": ap.description,
                "amount": float(ap.amount),
                "amount_paid": float(ap.amount_paid),
                "balance": float(ap.balance),
                "due_date": ap.due_date.isoformat() if ap.due_date else None,
                "status": ap.status,
            }
            for ap in ap_list
        ]


async def create_accounts_payable(
    tenant_id: str,
    bill_number: str,
    vendor_name: str,
    amount: float,
    due_date: str,
    description: str | None = None,
) -> dict:
    """Create a new Accounts Payable record.

    Tracks money owed to a vendor or supplier for goods or services received.

    Args:
        tenant_id: Unique identifier of the tenant.
        bill_number: The bill/invoice number from the vendor.
        vendor_name: Name of the vendor or supplier.
        amount: Total bill amount in NGN.
        due_date: Date by which payment should be made (ISO format).
        description: Optional description of what was purchased.

    Returns:
        A dictionary containing the newly created AP record details.
    """
    from app.accounting.schemas import AccountsPayableCreateCommand
    from app.accounting.service import plan_create_accounts_payable
    from app.accounting.repository import (
        create_accounts_payable as repo_create_ap,
    )

    command = AccountsPayableCreateCommand(
        tenant_id=UUID(tenant_id),
        bill_number=bill_number,
        vendor_name=vendor_name,
        amount=amount,
        due_date=datetime.fromisoformat(due_date),
        description=description,
    )

    result, ap_model = plan_create_accounts_payable(command)
    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        await repo_create_ap(session, ap_model)
        await session.commit()

    return result.model_dump(mode="json")


async def record_ap_payment(
    tenant_id: str,
    ap_id: str,
    amount: float,
    payment_date: str,
    notes: str | None = None,
) -> dict:
    """Record a payment against an Accounts Payable record.

    When the business pays a vendor:
    1. Updates the AP record (amount_paid, balance, status)
    2. Creates journal entries (Debit AP, Credit Cash)

    Args:
        tenant_id: Unique identifier of the tenant.
        ap_id: UUID of the AP record being paid.
        amount: Payment amount in NGN.
        payment_date: Date the payment was made (ISO format).
        notes: Optional notes about the payment.

    Returns:
        A dictionary containing the updated AP record details.
    """
    from app.accounting.repository import (
        get_accounts_payable_by_id,
        update_ap_payment,
        create_journal,
        create_journal_entry,
    )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        # Get the AP record
        ap = await get_accounts_payable_by_id(session, UUID(ap_id))
        if not ap:
            raise ValueError(f"Accounts Payable record not found: {ap_id}")

        # Update the AP record
        updated_ap = await update_ap_payment(session, UUID(ap_id), amount)

        # Create journal entry: Debit AP, Credit Cash
        from app.accounting.service import plan_record_ap_payment
        from app.accounting.schemas import PaymentRecordCommand

        command = PaymentRecordCommand(
            tenant_id=UUID(tenant_id),
            amount=amount,
            payment_date=datetime.fromisoformat(payment_date),
            notes=notes,
        )
        ap_result, journal, entries, events = plan_record_ap_payment(
            command, UUID(ap_id), UUID(tenant_id)
        )

        # Resolve account IDs
        from app.accounting.repository import get_account_by_code

        cash_account = await get_account_by_code(session, UUID(tenant_id), "1000")
        ap_account = await get_account_by_code(session, UUID(tenant_id), "2000")

        if cash_account and ap_account:
            # Update entries with correct account IDs
            for entry in entries:
                if entry.account_code == "1000":
                    entry.account_id = cash_account.id
                elif entry.account_code == "2000":
                    entry.account_id = ap_account.id

            # Create journal and entries
            await create_journal(session, journal)
            for entry in entries:
                entry.status = "posted"
                entry.posted_at = journal.posted_at
                await create_journal_entry(session, entry)

            # Post the journal
            from app.accounting.repository import post_journal as repo_post_journal

            await repo_post_journal(session, journal.id, None)
            updated_ap.journal_id = journal.id

        await session.commit()

    return {
        "id": str(updated_ap.id),
        "amount_paid": float(updated_ap.amount_paid),
        "balance": float(updated_ap.balance),
        "status": updated_ap.status,
    }


async def list_expenses(
    tenant_id: str,
    category: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[dict]:
    """List expenses with optional filtering.

    Args:
        tenant_id: Unique identifier of the tenant.
        category: Optional expense category to filter by.
        from_date: Optional start date for date range filtering.
        to_date: Optional end date for date range filtering.

    Returns:
        A list of dictionaries containing expense details.
    """
    from app.accounting.repository import list_expenses as repo_list_expenses

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        expenses = await repo_list_expenses(
            session, UUID(tenant_id), category, from_date, to_date
        )
        return [
            {
                "id": str(e.id),
                "expense_number": e.expense_number,
                "category": e.category,
                "description": e.description,
                "amount": float(e.amount),
                "vendor": e.vendor,
                "receipt_url": e.receipt_url,
                "expense_date": e.expense_date.isoformat() if e.expense_date else None,
                "account_id": str(e.account_id),
                "journal_id": str(e.journal_id) if e.journal_id else None,
                "created_by": str(e.created_by),
            }
            for e in expenses
        ]


async def create_expense(
    tenant_id: str,
    category: str,
    description: str,
    amount: float,
    expense_date: str,
    created_by: str,
    vendor: str | None = None,
    receipt_url: str | None = None,
) -> dict:
    """Record a new business expense.

    Automatically determines the expense account from the category and creates
    journal entries: Debit Expense Account, Credit Cash.

    Category to Account Mapping:
        rent -> 5100 (Rent)
        utilities -> 5200 (Utilities)
        salaries -> 5300 (Salaries)
        supplies -> 5400 (Supplies)
        transport -> 5500 (Transportation)
        marketing -> 5600 (Marketing)
        bank_charges -> 5700 (Bank Charges)
        phone_internet -> 5800 (Phone & Internet)
        maintenance/insurance/taxes/other -> 5900 (Miscellaneous)

    Args:
        tenant_id: Unique identifier of the tenant.
        category: Expense category (e.g., "rent", "utilities").
        description: Detailed description of the expense.
        amount: Expense amount in NGN.
        expense_date: Date the expense was incurred (ISO format).
        created_by: UUID of the user recording this expense.
        vendor: Optional name of the vendor/supplier.
        receipt_url: Optional URL to an uploaded receipt image.

    Returns:
        A dictionary containing the newly created expense details.

    Raises:
        ValueError: If the category is invalid or the expense account is not found.
    """
    from app.accounting.seed import EXPENSE_CATEGORY_ACCOUNT_MAP
    from app.accounting.schemas import ExpenseCreateCommand
    from app.accounting.service import plan_create_expense
    from app.accounting.repository import (
        create_expense as repo_create_expense,
        create_journal,
        create_journal_entry,
        get_account_by_code,
    )

    # Auto-determine account code from category
    account_code = EXPENSE_CATEGORY_ACCOUNT_MAP.get(category)
    if not account_code:
        raise ValueError(
            f"Invalid expense category: '{category}'. "
            f"Valid categories: {', '.join(EXPENSE_CATEGORY_ACCOUNT_MAP.keys())}"
        )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        # Look up the expense account UUID from the Chart of Accounts
        expense_account = await get_account_by_code(session, UUID(tenant_id), account_code)
        if not expense_account:
            raise ValueError(
                f"Expense account '{account_code}' not found for tenant. "
                f"Please seed the Chart of Accounts first."
            )

        account_id = expense_account.id

        command = ExpenseCreateCommand(
            tenant_id=UUID(tenant_id),
            category=category,
            description=description,
            amount=amount,
            expense_date=datetime.fromisoformat(expense_date),
            account_id=account_id,
            created_by=UUID(created_by),
            vendor=vendor,
            receipt_url=receipt_url,
        )

        result, expense_model, journal, entries, events = plan_create_expense(command)

        # Resolve the cash account ID
        cash_account = await get_account_by_code(session, UUID(tenant_id), "1000")
        if cash_account:
            # Update the cash entry with correct account ID
            for entry in entries:
                if entry.account_code == "1000":
                    entry.account_id = cash_account.id

            # Update the expense entry with correct account code
            for entry in entries:
                if entry.account_code == "":
                    entry.account_code = expense_account.code

        # Create journal and entries
        await create_journal(session, journal)
        for entry in entries:
            entry.status = "posted"
            entry.posted_at = journal.posted_at
            await create_journal_entry(session, entry)

        # Post the journal
        from app.accounting.repository import post_journal as repo_post_journal

        await repo_post_journal(session, journal.id, UUID(created_by))

        # Create the expense
        expense_model.journal_id = journal.id
        await repo_create_expense(session, expense_model)

        await session.commit()

    return result.model_dump(mode="json")


async def get_expense_summary(
    tenant_id: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict:
    """Get expense summary grouped by category.

    Args:
        tenant_id: Unique identifier of the tenant.
        from_date: Optional start date for date range filtering.
        to_date: Optional end date for date range filtering.

    Returns:
        A dictionary mapping category names to total amounts.
    """
    from app.accounting.repository import (
        get_expense_summary as repo_get_expense_summary,
    )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        summary = await repo_get_expense_summary(
            session, UUID(tenant_id), from_date, to_date
        )
        return {k: round(v, 2) for k, v in summary.items()}


async def get_financial_dashboard(tenant_id: str) -> dict:
    """Get key financial metrics for the dashboard.

    Returns:
        - cash_balance: Current cash balance in NGN
        - outstanding_receivable: Total AR (money owed by customers)
        - outstanding_payable: Total AP (money owed to vendors)
        - total_expenses_this_month: Total expenses for current month
        - expense_by_category: Expense breakdown by category

    Args:
        tenant_id: Unique identifier of the tenant.

    Returns:
        A dictionary containing all dashboard metrics.
    """
    from app.accounting.repository import (
        get_financial_dashboard as repo_get_dashboard,
    )

    sdb = _get_sdb("accounting")
    async with sdb.session() as session:
        return await repo_get_dashboard(session, UUID(tenant_id))


# ═══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════


async def send_notification(
    tenant_id: str,
    channel: str,
    recipient: str,
    subject: str | None,
    body: str,
    correlation_id: str | None = None,
) -> dict:
    """Send a notification to a recipient via the specified channel.

    Plans and persists a notification record through the notifications
    service. The notification is queued for delivery through the requested
    channel (e.g. "email", "sms", "whatsapp").

    Args:
        tenant_id: Unique identifier of the tenant sending the notification.
        channel: Delivery channel for the notification (e.g. "email",
            "sms", "whatsapp").
        recipient: Address or identifier of the notification recipient.
        subject: Optional subject line for the notification.
        body: Body content of the notification.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the notification details including ``id``,
        ``channel``, ``recipient``, and ``status``.
    """
    from app.notifications.schemas import NotificationSendCommand
    from app.notifications.service import plan_send_notification
    from app.notifications.repository import create_notification as repo_create_notification

    command = NotificationSendCommand(
        tenant_id=UUID(tenant_id),
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        correlation_id=correlation_id,
    )

    result, notification_model = plan_send_notification(command)
    sdb = _get_sdb("notifications")
    async with sdb.session() as session:
        await repo_create_notification(session, notification_model)
        await session.commit()

    return result.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════════
#  INVENTORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def adjust_stock(
    tenant_id: str,
    product_id: str,
    store_id: str,
    reason: str,
    qty_change: Decimal,
    unit_cost: Decimal | None = None,
    notes: str | None = None,
    created_by: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Adjust the stock quantity of a product in a specific store.

    Creates a stock adjustment record and updates the stock balance for the
    given product and store. The adjustment can be positive (stock in) or
    negative (stock out) depending on the ``qty_change`` value.

    Args:
        tenant_id: Unique identifier of the tenant.
        product_id: Unique identifier of the product to adjust.
        store_id: Unique identifier of the store where the adjustment
            occurs.
        reason: Reason for the adjustment (e.g. "restock", "damage",
            "correction").
        qty_change: Quantity to add (positive) or remove (negative) from
            the current stock.
        unit_cost: Optional cost per unit for valuation purposes.
        notes: Optional additional notes for the adjustment.
        created_by: Optional identifier of the user performing the
            adjustment.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``adjustment_id`` and ``new_balance``.

    Raises:
        ValueError: If the adjustment would result in negative stock.
    """
    from app.inventory.schemas import AdjustStockCommand
    from app.inventory.service import plan_adjust_stock
    from app.inventory.repository import (
        create_stock_adjustment,
        get_stock_balance,
        upsert_stock_balance,
    )

    command = AdjustStockCommand(
        tenant_id=UUID(tenant_id),
        product_id=UUID(product_id),
        store_id=UUID(store_id),
        reason=reason,
        qty_change=qty_change,
        unit_cost=unit_cost,
        notes=notes,
        created_by=UUID(created_by) if created_by else None,
        correlation_id=correlation_id,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        current = await get_stock_balance(session, UUID(product_id), UUID(store_id))
        adjustment, balance, outbox = plan_adjust_stock(command, current)
        await create_stock_adjustment(session, adjustment)
        await upsert_stock_balance(session, balance)
        await _record_movement(
            session,
            tenant_id=UUID(tenant_id),
            product_id=UUID(product_id),
            store_id=UUID(store_id),
            movement_type="adjustment",
            qty_change=qty_change,
            balance_before=Decimal(str(current.qty)) if current else Decimal("0"),
            balance_after=balance.qty,
            reference_type="adjustment",
            reference_id=adjustment.id,
            reason=reason,
            unit_cost=unit_cost,
            notes=notes,
            created_by=UUID(created_by) if created_by else None,
        )
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return {"adjustment_id": str(adjustment.id), "new_balance": balance.qty}


# ═══════════════════════════════════════════════════════════════════════════════
#  STORE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_store(
    tenant_id: str,
    name: str,
    address: str | None = None,
    is_warehouse: bool = False,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a new store or warehouse for a tenant.

    Plans and persists a new store through the stores service. The store
    can be a regular retail location or a warehouse that distributes stock
    to other stores. The tier policy determines whether the tenant is
    allowed to create additional stores.

    Args:
        tenant_id: Unique identifier of the tenant that owns the store.
        name: Display name of the store.
        address: Optional physical address of the store.
        is_warehouse: Whether this store functions as a warehouse
            (distribution centre) rather than a retail outlet.
        actor_id: Optional identifier of the user performing this action.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created store details including
        ``id``, ``name``, ``address``, ``is_warehouse``, and ``status``.

    Raises:
        ValueError: If the tenant's tier does not allow additional stores.
    """
    from app.stores.schemas import StoreCreateCommand
    from app.stores.service import plan_create_store
    from app.stores.repository import (
        create_store as repo_create_store,
        count_main_stores,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        main_count = await count_main_stores(session, UUID(tenant_id))

    command = StoreCreateCommand(
        tenant_id=UUID(tenant_id),
        name=name,
        address=address,
        is_warehouse=is_warehouse,
    )

    result, store_model = plan_create_store(command, existing_main_count=main_count)
    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        await repo_create_store(session, store_model)
        await session.commit()

    await cache.delete_pattern(f"sf:cache:stores:list:{tenant_id}*")
    return result.model_dump(mode="json")


@cached(prefix="stores:list", ttl=300, key_func=lambda tenant_id, **kw: tenant_id)
async def list_stores(tenant_id: str) -> list[dict]:
    """List all stores belonging to a tenant.

    Retrieves all stores from the inventory database for the given tenant,
    including both retail outlets and warehouses.

    Args:
        tenant_id: Unique identifier of the tenant whose stores are
            being listed.

    Returns:
        A list of store dictionaries, each containing ``id``, ``name``,
        ``address``, ``is_warehouse``, and ``status``.
    """
    from app.stores.repository import list_stores as repo_list_stores

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        stores = await repo_list_stores(session, UUID(tenant_id))
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "address": s.address,
                "is_warehouse": s.is_warehouse,
                "status": s.status,
            }
            for s in stores
        ]


async def get_store(tenant_id: str, store_id: str) -> dict | None:
    """Retrieve a specific store by its identifier.

    Fetches the store from the inventory database and validates that it
    belongs to the specified tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the store.
        store_id: Unique identifier of the store to retrieve.

    Returns:
        A dictionary containing the store details including ``id``, ``name``,
        ``address``, ``is_warehouse``, and ``status``, or ``None`` if the
        store does not exist or belongs to a different tenant.
    """
    from app.stores.repository import get_store_with_tenant

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        store = await get_store_with_tenant(session, UUID(store_id), UUID(tenant_id))
        if not store:
            return None
        return {
            "id": str(store.id),
            "name": store.name,
            "address": store.address,
            "is_warehouse": store.is_warehouse,
            "status": store.status,
        }


async def get_store_details(
    tenant_id: str,
    store_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """RPC: Get everything about a store in one call via Postgres stored function.

    Returns store info, products with stock levels, categories, and stats
    (sales, top products, inventory health).
    """
    from uuid import UUID
    from sqlalchemy import text

    tid = UUID(tenant_id)
    sid = UUID(store_id)

    sdb_stores = _get_sdb("stores")
    async with sdb_stores.session() as session:
        result = await session.execute(
            text(
                "SELECT get_store_details(:tenant_id, :store_id, :from_date, :to_date)"
            ),
            {
                "tenant_id": tid,
                "store_id": sid,
                "from_date": from_date,
                "to_date": to_date,
            },
        )
        row = result.scalar()
        return row if row else {}


async def sync_store_products(tenant_id: str, store_id: str) -> dict:
    """Synchronise product stock balances from the warehouse to a target store.

    Copies stock balance records from the tenant's warehouse to the target
    store for all products that do not already have a stock balance in that
    store. Products with existing balances are skipped. Initial quantities
    are set to zero.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the target store to sync products to.

    Returns:
        A dictionary containing ``synced`` (number of products newly added)
        and ``skipped`` (number of products already present).

    Raises:
        ValueError: If the tenant has no warehouse or the target store does
            not exist.
    """
    from app.inventory.models import StockBalance
    from app.inventory.repository import get_stock_balance
    from app.stores.repository import list_stores as repo_list_stores

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        stores = await repo_list_stores(session, UUID(tenant_id))
        warehouse = next((s for s in stores if s.is_warehouse), None)
        if not warehouse:
            raise ValueError("no_warehouse")
        target = next((s for s in stores if str(s.id) == store_id), None)
        if not target:
            raise ValueError("store_not_found")

        from sqlalchemy import select

        result = await session.execute(
            select(StockBalance).where(
                StockBalance.tenant_id == UUID(tenant_id),
                StockBalance.store_id == warehouse.id,
            )
        )
        warehouse_balances = list(result.scalars().all())

        synced = 0
        skipped = 0
        for wb in warehouse_balances:
            existing = await get_stock_balance(session, wb.product_id, UUID(store_id))
            if existing:
                skipped += 1
                continue
            balance = StockBalance(
                tenant_id=UUID(tenant_id),
                product_id=wb.product_id,
                store_id=UUID(store_id),
                qty=0,
                reserved_qty=0,
                min_stock_level=wb.min_stock_level,
                unit_cost=wb.unit_cost,
            )
            session.add(balance)
            synced += 1

        await session.commit()

    return {"synced": synced, "skipped": skipped}


@cached(
    prefix="store_products:list",
    ttl=30,
    key_func=lambda tenant_id, store_id, search=None, page=1, page_size=50, **kw: f"{store_id}:{search or ''}:{page}:{page_size}",
)
async def get_store_products(
    tenant_id: str,
    store_id: str,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List products available in a specific store with their stock balances.

    Retrieves a paginated list of products in the given store by joining
    stock balance records with product catalog data. Supports text search
    on product names.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store to query products for.
        search: Optional search term to filter products by name.
        page: Page number for pagination (1-indexed).
        page_size: Number of items per page.

    Returns:
        A dictionary containing ``items`` (list of product dictionaries with
        stock information), ``total`` count, ``page``, and ``page_size``.
    """
    from app.catalog.models import Product, Category
    from app.inventory.models import StockBalance

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        query = (
            select(
                StockBalance,
                Product.name,
                Product.public_id,
                Product.sku,
                Product.selling_price,
                Product.status,
                Product.qr_url,
                Category.name.label("category_name"),
            )
            .join(Product, StockBalance.product_id == Product.id)
            .join(Category, Product.category_id == Category.id, isouter=True)
            .where(
                StockBalance.tenant_id == UUID(tenant_id),
                StockBalance.store_id == UUID(store_id),
            )
        )
        if search:
            query = query.where(Product.name.ilike(f"%{search}%"))
        query = query.order_by(Product.name).limit(page_size).offset((page - 1) * page_size)
        result = await session.execute(query)
        rows = result.all()

        count_query = (
            select(func.count())
            .select_from(StockBalance)
            .join(Product, StockBalance.product_id == Product.id, isouter=True)
            .where(
                StockBalance.tenant_id == UUID(tenant_id),
                StockBalance.store_id == UUID(store_id),
            )
        )
        if search:
            count_query = count_query.where(Product.name.ilike(f"%{search}%"))
        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return {
            "items": [
                {
                    "id": str(row.StockBalance.product_id),
                    "product_id": str(row.StockBalance.product_id),
                    "public_id": row.public_id,
                    "name": row.name,
                    "sku": row.sku,
                    "selling_price": float(row.selling_price) if row.selling_price else None,
                    "status": row.status,
                    "category": row.category_name,
                    "qr_url": row.qr_url,
                    "qty": float(row.StockBalance.qty),
                    "available": float(row.StockBalance.qty) - float(row.StockBalance.reserved_qty),
                    "reserved_qty": float(row.StockBalance.reserved_qty),
                    "min_stock_level": float(row.StockBalance.min_stock_level),
                    "unit_cost": float(row.StockBalance.unit_cost)
                    if row.StockBalance.unit_cost
                    else None,
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def add_product_to_store(
    tenant_id: str,
    store_id: str,
    product_id: str,
    qty: float = 0,
) -> dict:
    """Add a product to a store's inventory with an initial stock quantity.

    Creates a new stock balance record linking the product to the store.
    The product must exist in the catalog and must not already have a
    stock balance in this store.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store to add the product to.
        product_id: Unique identifier of the product to add.
        qty: Initial stock quantity (defaults to 0).

    Returns:
        A dictionary containing ``product_id``, ``store_id``, and ``qty``.

    Raises:
        ValueError: If the product does not exist in the catalog or already
            has a stock balance in the specified store.
    """
    from app.catalog.repository import get_product_by_id as repo_get_product
    from app.inventory.models import StockBalance
    from app.inventory.repository import get_stock_balance

    sdb_catalog = _get_sdb("catalog")
    async with sdb_catalog.session() as cat_session:
        product = await repo_get_product(cat_session, UUID(product_id))
        if not product:
            raise ValueError("product_not_found")

    sdb_inventory = _get_sdb("inventory")
    async with sdb_inventory.session() as inv_session:
        existing = await get_stock_balance(inv_session, UUID(product_id), UUID(store_id))
        if existing:
            raise ValueError("product_already_in_store")

        balance = StockBalance(
            tenant_id=UUID(tenant_id),
            product_id=UUID(product_id),
            store_id=UUID(store_id),
            qty=qty,
            reserved_qty=0,
            min_stock_level=0,
            unit_cost=None,
        )
        inv_session.add(balance)
        await inv_session.commit()

    return {"product_id": product_id, "store_id": store_id, "qty": qty}


async def create_product_for_store(
    tenant_id: str,
    store_id: str,
    name: str,
    description: str | None = None,
    category_id: str | None = None,
    unit: str = "unit",
    cost_price: Decimal = Decimal("0"),
    selling_price: Decimal = Decimal("0"),
    tax_rate: Decimal | None = None,
    reorder_point: int = 0,
    image_url: str | None = None,
    qty: float = 0,
    created_by: str | None = None,
) -> dict:
    """Create a new product in the catalog and add it to a store.

    Creates the product in catalog.products, then creates a store_products
    record and a stock_balances record for the specified store.
    """
    from app.catalog.models import Product
    from app.catalog.ids import new_product_public_id
    from app.inventory.models import StockBalance
    from app.stores.models import StoreProduct

    sdb_catalog = _get_sdb("catalog")
    _random = generate_random_timestamp_string()
    sku = f'{name}-{_random}'.upper().replace(" ", "")
    async with sdb_catalog.session() as session:
        product = Product(
            tenant_id=UUID(tenant_id),
            public_id=new_product_public_id(),
            name=name,
            sku=sku,
            description=description,
            category_id=UUID(category_id) if category_id else None,
            unit=unit,
            cost_price=cost_price,
            selling_price=selling_price,
            tax_rate=tax_rate,
            reorder_point=reorder_point,
            image_url=image_url,
        )
        session.add(product)
        await session.flush()
        await session.commit()

    sdb_stores = _get_sdb("stores")
    async with sdb_stores.session() as session:
        store_product = StoreProduct(
            tenant_id=UUID(tenant_id),
            store_id=UUID(store_id),
            product_id=product.id,
            name=name,
            sku=sku,
            selling_price=float(selling_price),
            cost_price=float(cost_price) if cost_price else 0,
            tax_rate=float(tax_rate) if tax_rate else None,
            reorder_point=reorder_point,
            image_url=image_url,
            status="active",
        )
        session.add(store_product)
        await session.commit()

    sdb_inventory = _get_sdb("inventory")
    async with sdb_inventory.session() as session:
        balance = StockBalance(
            tenant_id=UUID(tenant_id),
            product_id=product.id,
            store_id=UUID(store_id),
            qty=Decimal(str(qty)),
            reserved_qty=Decimal("0"),
            min_stock_level=Decimal("0"),
            unit_cost=cost_price if cost_price else None,
        )
        session.add(balance)

        if qty > 0:
            from app.inventory.models import StockAdjustment
            adjustment = StockAdjustment(
                tenant_id=UUID(tenant_id),
                product_id=product.id,
                store_id=UUID(store_id),
                reason="initial_stock",
                qty_change=Decimal(str(qty)),
                unit_cost=cost_price if cost_price else None,
                notes="Initial stock on product creation",
                created_by=UUID(created_by) if created_by else None,
            )
            session.add(adjustment)

        await session.commit()

    qr_url = None
    qr_asset_id = None
    try:
        from app.catalog.qr import build_product_qr_url, generate_qr_png
        from app.catalog.cloudinary_upload import upload_qr_png

        qr_payload = build_product_qr_url(
            base_url=settings.api_base_url,
            store_id=store_id,
            product_id=str(product.id),
        )
        png_bytes = generate_qr_png(qr_payload, box_size=20, border=4)
        upload = upload_qr_png(
            tenant_id=tenant_id,
            product_id=str(product.id),
            png_bytes=png_bytes,
        )
        qr_url = upload.get("url")
        qr_asset_id = upload.get("public_id")

        if qr_url:
            async with sdb_catalog.session() as session:
                from app.catalog.repository import update_product
                await update_product(
                    session,
                    product.id,
                    qr_url=qr_url,
                    qr_asset_id=qr_asset_id,
                    qr_payload=qr_payload,
                )
                await session.commit()
    except Exception:
        logger = logging.getLogger("QR.generation.product")
        logger.exception("QR generation failed for product %s", product.id)

    await cache.delete_pattern(f"sf:cache:store_products:list:{tenant_id}:{store_id}*")
    return {
        "product_id": str(product.id),
        "store_id": store_id,
        "name": name,
        "sku": sku,
        "selling_price": float(selling_price),
        "qty": qty,
        "qr_url": qr_url,
        "qr_payload": qr_payload if qr_url else None,
    }


async def update_store(
    tenant_id: str,
    store_id: str,
    name: str | None = None,
    address: str | None = None,
    is_warehouse: bool | None = None,
) -> dict | None:
    """Update an existing store's details.

    Modifies the name, address, or warehouse status of a store. When
    converting a store to a warehouse, the operation is blocked if the
    tenant already has other main (non-warehouse) stores.

    Args:
        tenant_id: Unique identifier of the tenant that owns the store.
        store_id: Unique identifier of the store to update.
        name: Optional new display name for the store.
        address: Optional new physical address.
        is_warehouse: Optional flag to toggle warehouse status.

    Returns:
        A dictionary containing the updated store details, or ``None`` if
        the store does not exist.

    Raises:
        ValueError: If attempting to convert to a warehouse while main
            stores exist.
    """
    from app.stores.repository import get_store_with_tenant, count_main_stores

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        store = await get_store_with_tenant(session, UUID(store_id), UUID(tenant_id))
        if not store:
            return None

        if is_warehouse is not None and is_warehouse != store.is_warehouse:
            if is_warehouse:
                main_count = await count_main_stores(session, UUID(tenant_id))
                if main_count > 0:
                    raise ValueError("main_store_exists")
            store.is_warehouse = is_warehouse

        if name is not None:
            store.name = name
        if address is not None:
            store.address = address

        await session.commit()
        await cache.delete_pattern(f"sf:cache:stores:list:{tenant_id}*")
        return {
            "id": str(store.id),
            "name": store.name,
            "address": store.address,
            "is_warehouse": store.is_warehouse,
            "status": store.status,
        }


async def delete_store(tenant_id: str, store_id: str) -> dict | None:
    """Soft-delete a store by marking its status as deleted.

    Sets the store status to "deleted" and clears the warehouse flag. The
    store record is not physically removed from the database.

    Args:
        tenant_id: Unique identifier of the tenant that owns the store.
        store_id: Unique identifier of the store to delete.

    Returns:
        A dictionary containing ``ok: True`` on success, or ``None`` if the
        store does not exist.
    """
    from app.stores.repository import get_store_with_tenant

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        store = await get_store_with_tenant(session, UUID(store_id), UUID(tenant_id))
        if not store:
            return None
        store.status = "deleted"
        store.is_warehouse = False
        await session.commit()
        await cache.delete_pattern(f"sf:cache:stores:list:{tenant_id}*")
        return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  STORE PRODUCT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_store_product(
    tenant_id: str,
    store_id: str,
    product_id: str,
    name: str,
    selling_price: float,
    sku: str | None = None,
    cost_price: float = 0,
    tax_rate: float | None = None,
    reorder_point: int = 0,
    image_url: str | None = None,
    status: str = "active",
    extra_metadata: dict | None = None,
) -> dict:
    """Create a store-specific product record.

    Creates a StoreProduct linking a catalog product to a specific store
    with store-specific pricing, name, and settings. Auto-generates a
    unique SKU if not provided.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store.
        product_id: Unique identifier of the catalog product.
        name: Store-specific product display name.
        selling_price: Store-specific selling price.
        sku: Optional SKU — auto-generated if omitted.
        cost_price: Store-specific cost price.
        tax_rate: Optional store-specific tax rate.
        reorder_point: Minimum stock level for alerts.
        image_url: Optional store-specific product image URL.
        status: Product status within this store.
        extra_metadata: Optional additional store-specific product data.

    Returns:
        A dictionary containing the created store product details.

    Raises:
        ValueError: If the store or product does not exist, or if the
            product already exists in the store.
    """
    from app.stores.models import StoreProduct
    from app.stores.repository import (
        get_store_product,
        create_store_product as repo_create,
        get_store_with_tenant,
    )
    from app.stores.sku import generate_unique_sku
    from app.catalog.repository import get_product_by_id

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        store = await get_store_with_tenant(session, UUID(store_id), UUID(tenant_id))
        if not store:
            raise ValueError("store_not_found")

        existing = await get_store_product(session, UUID(store_id), UUID(product_id))
        if existing:
            raise ValueError("product_already_in_store")

        if not sku:
            sku = await generate_unique_sku(session, UUID(tenant_id))

        store_product = StoreProduct(
            tenant_id=UUID(tenant_id),
            store_id=UUID(store_id),
            product_id=UUID(product_id),
            name=name,
            sku=sku,
            selling_price=selling_price,
            cost_price=cost_price,
            tax_rate=tax_rate,
            reorder_point=reorder_point,
            image_url=image_url,
            status=status,
            extra_metadata=extra_metadata,
        )
        result = await repo_create(session, store_product)
        await session.commit()

    return {
        "id": str(result.id),
        "store_id": str(result.store_id),
        "product_id": str(result.product_id),
        "name": result.name,
        "sku": result.sku,
        "selling_price": float(result.selling_price),
        "cost_price": float(result.cost_price),
        "status": result.status,
    }


async def update_store_product(
    tenant_id: str,
    store_id: str,
    product_id: str,
    **kwargs,
) -> dict | None:
    """Update a store-specific product record.

    Modifies store-specific fields on a StoreProduct. Only provided
    fields are updated.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store.
        product_id: Unique identifier of the catalog product.
        **kwargs: Fields to update (e.g. name, selling_price, sku).

    Returns:
        A dictionary containing the updated store product details, or
        None if not found.

    Raises:
        ValueError: If the store or product does not exist.
    """
    from app.stores.repository import (
        get_store_product,
        update_store_product as repo_update,
        get_store_with_tenant,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        store = await get_store_with_tenant(session, UUID(store_id), UUID(tenant_id))
        if not store:
            raise ValueError("store_not_found")

        existing = await get_store_product(session, UUID(store_id), UUID(product_id))
        if not existing:
            return None

        result = await repo_update(session, existing.id, **kwargs)
        await session.commit()
        await cache.delete_pattern(f"sf:cache:store_products:list:{tenant_id}:{store_id}*")

    return {
        "id": str(result.id),
        "store_id": str(result.store_id),
        "product_id": str(result.product_id),
        "name": result.name,
        "sku": result.sku,
        "selling_price": float(result.selling_price),
        "cost_price": float(result.cost_price),
        "status": result.status,
    }


async def get_store_product(
    tenant_id: str,
    store_id: str,
    product_id: str,
) -> dict | None:
    """Get a single product in a store with stock info and recent history via RPC."""
    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        result = await session.execute(
            text("SELECT get_store_product_detail(:tid, :sid, :pid)"),
            {"tid": UUID(tenant_id), "sid": UUID(store_id), "pid": UUID(product_id)},
        )
        row = result.scalar()
        if not row or row == {}:
            return None
        data = dict(row)
        prod = data.get("product") or {}
        stock = data.get("stock") or {}
        raw_history = data.get("recent_history") or []
        history = [
            {
                "id": str(h.get("id", "")),
                "product_id": product_id,
                "store_id": store_id,
                "movement_type": h.get("movement_type", ""),
                "qty_change": float(h.get("qty_change", 0)),
                "balance_before": float(h.get("balance_before", 0)),
                "balance_after": float(h.get("balance_after", 0)),
                "reason": h.get("reason"),
                "created_at": h.get("created_at", ""),
            }
            for h in raw_history
        ]
        return {
            "id": str(prod.get("id", "")),
            "name": prod.get("name", ""),
            "sku": prod.get("sku"),
            "selling_price": float(prod.get("selling_price", 0)),
            "cost_price": float(prod.get("cost_price", 0)),
            "image_url": prod.get("image_url"),
            "status": prod.get("status", "active"),
            "qty": float(stock.get("qty", 0)),
            "reserved_qty": float(stock.get("reserved_qty", 0)),
            "available": float(stock.get("available", 0)),
            "min_stock_level": float(stock.get("min_stock_level", 0)),
            "unit_cost": None,
            "qr_url": data.get("qr_url"),
            "category": data.get("category"),
            "history": history,
        }


async def delete_store_product(
    tenant_id: str,
    store_id: str,
    product_id: str,
) -> bool:
    """Remove a product from a store (deletes stock balance + store_product)."""
    from app.inventory.models import StockBalance
    from app.inventory.repository import get_stock_balance
    from app.stores.models import StoreProduct

    sdb_inventory = _get_sdb("inventory")
    async with sdb_inventory.session() as session:
        balance = await get_stock_balance(session, UUID(product_id), UUID(store_id))
        if not balance:
            raise ValueError("product_not_in_store")
        if float(balance.reserved_qty) > 0:
            raise ValueError("has_reserved_stock")
        await session.delete(balance)
        await session.commit()

    sdb_stores = _get_sdb("stores")
    async with sdb_stores.session() as session:
        result = await session.execute(
            select(StoreProduct).where(
                StoreProduct.tenant_id == UUID(tenant_id),
                StoreProduct.store_id == UUID(store_id),
                StoreProduct.product_id == UUID(product_id),
            )
        )
        sp = result.scalar_one_or_none()
        if sp:
            await session.delete(sp)
            await session.commit()

    await cache.delete_pattern(f"sf:cache:store_products:list:{tenant_id}:{store_id}*")
    return True


async def list_store_products(
    tenant_id: str,
    store_id: str,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List products available in a specific store.

    Retrieves a paginated list of store products for the given store,
    with optional status and search filtering.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store.
        status: Optional status filter.
        search: Optional search term matching product name.
        page: Page number (1-indexed).
        page_size: Number of results per page.

    Returns:
        A dictionary containing items, total, page, and page_size.
    """
    from app.stores.repository import list_store_products as repo_list

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        result = await repo_list(
            session,
            UUID(tenant_id),
            UUID(store_id),
            status=status,
            search=search,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [
                {
                    "id": str(sp.id),
                    "store_id": str(sp.store_id),
                    "product_id": str(sp.product_id),
                    "name": sp.name,
                    "sku": sp.sku,
                    "selling_price": float(sp.selling_price),
                    "cost_price": float(sp.cost_price),
                    "tax_rate": float(sp.tax_rate) if sp.tax_rate else None,
                    "reorder_point": sp.reorder_point,
                    "image_url": sp.image_url,
                    "status": sp.status,
                }
                for sp in result["items"]
            ],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }


async def get_store_product_detail(
    tenant_id: str,
    store_id: str,
    product_id: str,
) -> dict | None:
    """Retrieve a specific store product record.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store.
        product_id: Unique identifier of the catalog product.

    Returns:
        A dictionary containing the store product details, or None if
        not found.
    """
    from app.stores.repository import get_store_product

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        sp = await get_store_product(session, UUID(store_id), UUID(product_id))
        if not sp or str(sp.tenant_id) != tenant_id:
            return None
        return {
            "id": str(sp.id),
            "store_id": str(sp.store_id),
            "product_id": str(sp.product_id),
            "name": sp.name,
            "sku": sp.sku,
            "selling_price": float(sp.selling_price),
            "cost_price": float(sp.cost_price),
            "tax_rate": float(sp.tax_rate) if sp.tax_rate else None,
            "reorder_point": sp.reorder_point,
            "image_url": sp.image_url,
            "status": sp.status,
        }


async def sync_store_products(
    tenant_id: str,
    target_store_id: str,
    product_ids: list[str] | None = None,
    from_store_id: str | None = None,
    sync_all: bool = False,
) -> dict:
    """Sync products to a target store from a source store or catalog template.

    Creates StoreProduct records in the target store for the specified
    products. When ``from_store_id`` is provided, values are copied from
    that store's records. When omitted, values are copied from the
    catalog template.

    Args:
        tenant_id: Unique identifier of the tenant.
        target_store_id: Unique identifier of the target store.
        product_ids: Optional list of product UUIDs to sync.
        from_store_id: Optional source store UUID. When None, uses the
            catalog template.
        sync_all: When True, syncs all catalog products.

    Returns:
        A dictionary containing synced count, skipped count, and any
        errors.

    Raises:
        ValueError: If the target store does not exist, or if neither
            product_ids nor sync_all is provided.
    """
    from app.stores.models import StoreProduct
    from app.stores.repository import (
        get_store_product,
        get_store_with_tenant,
        create_store_product as repo_create,
    )
    from app.stores.sku import generate_unique_sku
    from app.catalog.models import Product
    from app.catalog.repository import list_products

    if not product_ids and not sync_all:
        raise ValueError("provide_product_ids_or_sync_all")

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        target = await get_store_with_tenant(session, UUID(target_store_id), UUID(tenant_id))
        if not target:
            raise ValueError("target_store_not_found")

        source = None
        if from_store_id:
            source = await get_store_with_tenant(session, UUID(from_store_id), UUID(tenant_id))
            if not source:
                raise ValueError("source_store_not_found")

    # Get products to sync
    sdb_catalog = _get_sdb("catalog")
    async with sdb_catalog.session() as cat_session:
        if sync_all:
            catalog_products = await list_products(
                cat_session, UUID(tenant_id), status="active", limit=10000
            )
            product_uids = [p.id for p in catalog_products]
        else:
            product_uids = [UUID(pid) for pid in product_ids]

    synced = 0
    skipped = 0
    errors = []

    sdb_inv = _get_sdb("inventory")
    async with sdb_inv.session() as session:
        for pid in product_uids:
            try:
                # Check if already exists in target
                existing = await get_store_product(session, UUID(target_store_id), pid)
                if existing:
                    skipped += 1
                    continue

                if source:
                    # Copy from source store
                    source_sp = await get_store_product(session, UUID(from_store_id), pid)
                    if not source_sp:
                        skipped += 1
                        continue
                    name = source_sp.name
                    selling_price = float(source_sp.selling_price)
                    cost_price = float(source_sp.cost_price)
                    tax_rate = float(source_sp.tax_rate) if source_sp.tax_rate else None
                    reorder_point = source_sp.reorder_point
                    image_url = source_sp.image_url
                else:
                    # Copy from catalog template
                    sdb_cat = _get_sdb("catalog")
                    async with sdb_cat.session() as cat_session:
                        from app.catalog.repository import get_product_by_id

                        catalog_product = await get_product_by_id(cat_session, pid)
                        if not catalog_product:
                            errors.append(f"product_not_found:{pid}")
                            continue
                    name = catalog_product.name
                    selling_price = float(catalog_product.selling_price)
                    cost_price = 0
                    tax_rate = None
                    reorder_point = 0
                    image_url = None

                auto_sku = await generate_unique_sku(session, UUID(tenant_id))
                store_product = StoreProduct(
                    tenant_id=UUID(tenant_id),
                    store_id=UUID(target_store_id),
                    product_id=pid,
                    name=name,
                    sku=auto_sku,
                    selling_price=selling_price,
                    cost_price=cost_price,
                    tax_rate=tax_rate,
                    reorder_point=reorder_point,
                    image_url=image_url,
                    status="active",
                )
                await repo_create(session, store_product)
                synced += 1
            except Exception as exc:
                errors.append(f"{pid}:{str(exc)}")

        await session.commit()

    return {"synced": synced, "skipped": skipped, "errors": errors}


# ═══════════════════════════════════════════════════════════════════════════════
#  STOCK TRANSFERS
# ═══════════════════════════════════════════════════════════════════════════════


async def transfer_stock(
    tenant_id: str,
    product_id: str,
    from_store_id: str,
    to_store_id: str,
    qty: Decimal,
    notes: str | None = None,
    created_by: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Transfer stock of a product from one store to another.

    Moves a specified quantity of a product from the source store to the
    destination store. The source store must be a warehouse, and must have
    sufficient available stock (quantity minus reserved quantity). Creates
    paired stock adjustment records for both the source and destination.

    Args:
        tenant_id: Unique identifier of the tenant.
        product_id: Unique identifier of the product to transfer.
        from_store_id: Unique identifier of the source (warehouse) store.
        to_store_id: Unique identifier of the destination store.
        qty: Quantity to transfer.
        notes: Optional notes for the transfer.
        created_by: Optional identifier of the user performing the transfer.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``from_adjustment_id``,
        ``to_adjustment_id``, ``from_new_balance``, and ``to_new_balance``.

    Raises:
        ValueError: If the source store is not a warehouse, the stores are
            the same, the source balance is not found, or there is
            insufficient stock.
    """
    from app.inventory.schemas import TransferStockCommand
    from app.inventory.service import plan_transfer_stock
    from app.inventory.repository import (
        create_stock_adjustment,
        get_stock_balance,
        upsert_stock_balance,
    )
    from app.stores.repository import get_store_with_tenant

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        from_store = await get_store_with_tenant(session, UUID(from_store_id), UUID(tenant_id))
        if not from_store:
            raise ValueError("source_store_not_found")

        to_store = await get_store_with_tenant(session, UUID(to_store_id), UUID(tenant_id))
        if not to_store:
            raise ValueError("destination_store_not_found")

        if from_store_id == to_store_id:
            raise ValueError("same_store")

        if not from_store.is_warehouse:
            raise ValueError("only_main_can_distribute")

        from_balance = await get_stock_balance(session, UUID(product_id), UUID(from_store_id))
        if not from_balance:
            raise ValueError("source_balance_not_found")

        available = float(from_balance.qty) - float(from_balance.reserved_qty)
        if available < float(qty):
            raise ValueError(f"insufficient_stock:available_{available}")

        to_balance = await get_stock_balance(session, UUID(product_id), UUID(to_store_id))

        command = TransferStockCommand(
            tenant_id=UUID(tenant_id),
            product_id=UUID(product_id),
            from_store_id=UUID(from_store_id),
            to_store_id=UUID(to_store_id),
            qty=qty,
            notes=notes,
            created_by=UUID(created_by) if created_by else None,
            correlation_id=correlation_id,
        )

        from_adj, to_adj, from_bal, to_bal, outbox = plan_transfer_stock(
            command, from_balance, to_balance
        )
        await create_stock_adjustment(session, from_adj)
        await create_stock_adjustment(session, to_adj)
        await upsert_stock_balance(session, from_bal)
        await upsert_stock_balance(session, to_bal)
        await _record_movement(
            session,
            tenant_id=UUID(tenant_id),
            product_id=UUID(product_id),
            store_id=UUID(from_store_id),
            movement_type="transfer_out",
            qty_change=-qty,
            balance_before=Decimal(str(from_balance.qty)) if from_balance else Decimal("0"),
            balance_after=from_bal.qty,
            reference_type="transfer",
            reference_id=from_adj.id,
            reason="warehouse_distribution",
            notes=notes,
            created_by=UUID(created_by) if created_by else None,
        )
        await _record_movement(
            session,
            tenant_id=UUID(tenant_id),
            product_id=UUID(product_id),
            store_id=UUID(to_store_id),
            movement_type="transfer_in",
            qty_change=qty,
            balance_before=Decimal(str(to_balance.qty)) if to_balance else Decimal("0"),
            balance_after=to_bal.qty,
            reference_type="transfer",
            reference_id=to_adj.id,
            reason="warehouse_distribution",
            notes=notes,
            created_by=UUID(created_by) if created_by else None,
        )
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return {
        "from_adjustment_id": str(from_adj.id),
        "to_adjustment_id": str(to_adj.id),
        "from_new_balance": float(from_bal.qty),
        "to_new_balance": float(to_bal.qty),
    }


async def create_transfer_request(
    tenant_id: str,
    product_id: str,
    requesting_store_id: str,
    supplying_store_id: str,
    requested_qty: Decimal,
    notes: str | None = None,
    created_by: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a stock transfer request from one store to another.

    Initiates a request for the supplying store to transfer a specified
    quantity of a product to the requesting store. The request starts in
    "pending" status and must be approved before fulfilment.

    Args:
        tenant_id: Unique identifier of the tenant.
        product_id: Unique identifier of the product to transfer.
        requesting_store_id: Unique identifier of the store requesting
            stock.
        supplying_store_id: Unique identifier of the store that will
            supply the stock.
        requested_qty: Quantity of stock requested.
        notes: Optional notes for the transfer request.
        created_by: Optional identifier of the user creating the request.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``request_id``, ``status``, ``product_id``,
        ``requesting_store_id``, ``supplying_store_id``, and
        ``requested_qty``.

    Raises:
        ValueError: If either store is not found or the requesting store
            attempts to request from itself.
    """
    from app.inventory.schemas import TransferRequestCreateCommand
    from app.inventory.service import plan_create_transfer_request
    from app.inventory.repository import (
        create_transfer_request as repo_create_transfer_request,
        get_stock_balance,
    )
    from app.stores.repository import get_store_with_tenant

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        requesting_store = await get_store_with_tenant(
            session, UUID(requesting_store_id), UUID(tenant_id)
        )
        if not requesting_store:
            raise ValueError("requesting_store_not_found")

        supplying_store = await get_store_with_tenant(
            session, UUID(supplying_store_id), UUID(tenant_id)
        )
        if not supplying_store:
            raise ValueError("supplying_store_not_found")

        if requesting_store.id == UUID(supplying_store_id):
            raise ValueError("cannot_request_from_self")

        supplying_balance = await get_stock_balance(
            session, UUID(product_id), UUID(supplying_store_id)
        )

    command = TransferRequestCreateCommand(
        tenant_id=UUID(tenant_id),
        product_id=UUID(product_id),
        requesting_store_id=requesting_store.id,
        supplying_store_id=UUID(supplying_store_id),
        requested_qty=requested_qty,
        notes=notes,
        created_by=UUID(created_by) if created_by else None,
        correlation_id=correlation_id,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        request, outbox = plan_create_transfer_request(
            command,
            supplying_balance,
            requesting_is_warehouse=requesting_store.is_warehouse,
            supplying_is_warehouse=supplying_store.is_warehouse,
        )
        await repo_create_transfer_request(session, request)
        session.add(outbox.to_model())
        await session.commit()

    return {
        "request_id": str(request.id),
        "status": request.status,
        "product_id": str(request.product_id),
        "requesting_store_id": str(request.requesting_store_id),
        "supplying_store_id": str(request.supplying_store_id),
        "requested_qty": float(request.requested_qty),
    }


async def approve_transfer_request(
    tenant_id: str,
    request_id: str,
    approved_qty: Decimal,
    rejection_reason: str | None = None,
    approved_by: str | None = None,
    notes: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Approve or reject a pending stock transfer request.

    Processes a transfer request by either approving it with a specified
    quantity (which may differ from the requested amount) or rejecting it
    with a reason. Approval does not yet move stock; fulfilment is a
    separate step.

    Args:
        tenant_id: Unique identifier of the tenant.
        request_id: Unique identifier of the transfer request to approve.
        approved_qty: Quantity approved for transfer.
        rejection_reason: Optional reason if the request is being rejected
            instead of approved.
        approved_by: Optional identifier of the user approving the request.
        notes: Optional additional notes for the approval.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``request_id``, ``status``, and
        ``approved_qty``.

    Raises:
        ValueError: If the transfer request is not found.
    """
    from app.inventory.schemas import TransferRequestApproveCommand
    from app.inventory.service import plan_approve_transfer_request
    from app.inventory.repository import (
        get_transfer_request_by_id,
        get_stock_balance,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        request = await get_transfer_request_by_id(session, UUID(request_id))
        if not request or str(request.tenant_id) != tenant_id:
            raise ValueError("request_not_found")

        supplying_balance = await get_stock_balance(
            session, request.product_id, request.supplying_store_id
        )

        command = TransferRequestApproveCommand(
            tenant_id=UUID(tenant_id),
            request_id=UUID(request_id),
            approved_qty=approved_qty,
            rejection_reason=rejection_reason,
            approved_by=UUID(approved_by) if approved_by else None,
            notes=notes,
            correlation_id=correlation_id,
        )

        updated_request, outbox = plan_approve_transfer_request(command, request, supplying_balance)
        session.add(outbox.to_model())
        await session.commit()

    return {
        "request_id": str(updated_request.id),
        "status": updated_request.status,
        "approved_qty": float(updated_request.approved_qty)
        if updated_request.approved_qty
        else None,
    }


async def set_min_stock_level(
    tenant_id: str,
    store_id: str,
    product_id: str,
    min_stock_level: float,
) -> dict:
    """Set the minimum stock level (reorder point) for a product in a store.

    Updates the minimum stock level threshold used for low-stock alerts and
    reorder recommendations.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store.
        product_id: Unique identifier of the product.
        min_stock_level: Minimum quantity threshold below which the product
            is considered low in stock.

    Returns:
        A dictionary containing ``store_id``, ``product_id``, and
        ``min_stock_level``.

    Raises:
        ValueError: If the store or stock balance is not found.
    """
    from app.inventory.repository import (
        get_stock_balance,
        update_stock_balance_min_level,
    )
    from app.stores.repository import get_store_with_tenant

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        store = await get_store_with_tenant(session, UUID(store_id), UUID(tenant_id))
        if not store:
            raise ValueError("store_not_found")

        balance = await get_stock_balance(session, UUID(product_id), UUID(store_id))
        if not balance:
            raise ValueError("stock_balance_not_found")

        await update_stock_balance_min_level(
            session, UUID(store_id), UUID(product_id), min_stock_level
        )
        await session.commit()

    return {"store_id": store_id, "product_id": product_id, "min_stock_level": min_stock_level}


async def fulfill_transfer_request(
    tenant_id: str,
    request_id: str,
    correlation_id: str | None = None,
) -> dict:
    """Fulfil an approved stock transfer request.

    Executes the stock movement for an approved transfer request. Decrements
    stock from the supplying store and increments it at the requesting store,
    creating paired adjustment records. The request status is updated to
    "fulfilled".

    Args:
        tenant_id: Unique identifier of the tenant.
        request_id: Unique identifier of the approved transfer request to
            fulfil.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``request_id``, ``status``,
        ``from_adjustment_id``, ``to_adjustment_id``,
        ``from_new_balance``, and ``to_new_balance``.

    Raises:
        ValueError: If the transfer request is not found or the supplying
            store's stock balance is missing.
    """
    from app.inventory.service import plan_fulfill_transfer_request
    from app.inventory.repository import (
        get_transfer_request_by_id,
        get_stock_balance,
        create_stock_adjustment,
        upsert_stock_balance,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        request = await get_transfer_request_by_id(session, UUID(request_id))
        if not request or str(request.tenant_id) != tenant_id:
            raise ValueError("request_not_found")

        from_balance = await get_stock_balance(
            session, request.product_id, request.supplying_store_id
        )
        if not from_balance:
            raise ValueError("supplying_balance_not_found")

        to_balance = await get_stock_balance(
            session, request.product_id, request.requesting_store_id
        )

        from_adj, to_adj, from_bal, to_bal, updated_request, outbox = plan_fulfill_transfer_request(
            request=request,
            from_balance=from_balance,
            to_balance=to_balance,
            tenant_id=UUID(tenant_id),
            correlation_id=correlation_id,
        )

        await create_stock_adjustment(session, from_adj)
        await create_stock_adjustment(session, to_adj)
        await upsert_stock_balance(session, from_bal)
        await upsert_stock_balance(session, to_bal)
        await _record_movement(
            session,
            tenant_id=UUID(tenant_id),
            product_id=request.product_id,
            store_id=request.supplying_store_id,
            movement_type="transfer_out",
            qty_change=Decimal(str(-request.approved_qty)),
            balance_before=Decimal(str(from_balance.qty)),
            balance_after=from_bal.qty,
            reference_type="transfer",
            reference_id=from_adj.id,
            reason="transfer_request_fulfilled",
            created_by=None,
        )
        await _record_movement(
            session,
            tenant_id=UUID(tenant_id),
            product_id=request.product_id,
            store_id=request.requesting_store_id,
            movement_type="transfer_in",
            qty_change=Decimal(str(request.approved_qty)),
            balance_before=Decimal(str(to_balance.qty)) if to_balance else Decimal("0"),
            balance_after=to_bal.qty,
            reference_type="transfer",
            reference_id=to_adj.id,
            reason="transfer_request_fulfilled",
            created_by=None,
        )
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return {
        "request_id": str(updated_request.id),
        "status": updated_request.status,
        "from_adjustment_id": str(from_adj.id),
        "to_adjustment_id": str(to_adj.id),
        "from_new_balance": float(from_bal.qty),
        "to_new_balance": float(to_bal.qty),
    }


async def list_transfer_requests(
    tenant_id: str,
    status: str | None = None,
    store_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List stock transfer requests with optional filtering and pagination.

    Retrieves transfer requests from the inventory database, optionally
    filtered by status and store identifier. Results are paginated.

    Args:
        tenant_id: Unique identifier of the tenant.
        status: Optional status filter (e.g. "pending", "approved",
            "fulfilled", "rejected").
        store_id: Optional store identifier to filter requests involving
            that store (as either requester or supplier).
        page: Page number for pagination (1-indexed).
        page_size: Number of items per page.

    Returns:
        A dictionary containing ``items`` (list of transfer request
        dictionaries), ``total`` count, ``page``, and ``page_size``.
    """
    from app.inventory.repository import (
        list_transfer_requests as repo_list_transfer_requests,
        count_transfer_requests,
    )

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        items = await repo_list_transfer_requests(
            session,
            tenant_id=UUID(tenant_id),
            status=status,
            store_id=UUID(store_id) if store_id else None,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await count_transfer_requests(
            session,
            tenant_id=UUID(tenant_id),
            status=status,
            store_id=UUID(store_id) if store_id else None,
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "product_id": str(r.product_id),
                    "requesting_store_id": str(r.requesting_store_id),
                    "supplying_store_id": str(r.supplying_store_id),
                    "requested_qty": float(r.requested_qty),
                    "approved_qty": float(r.approved_qty) if r.approved_qty else None,
                    "status": r.status,
                    "notes": r.notes,
                    "rejection_reason": r.rejection_reason,
                    "created_by": str(r.created_by) if r.created_by else None,
                    "approved_by": str(r.approved_by) if r.approved_by else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def get_transfer_request(tenant_id: str, request_id: str) -> dict | None:
    """Retrieve a specific stock transfer request by its identifier.

    Fetches the transfer request from the inventory database and validates
    that it belongs to the specified tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the request.
        request_id: Unique identifier of the transfer request to retrieve.

    Returns:
        A dictionary containing the transfer request details including
        ``id``, ``product_id``, ``requesting_store_id``,
        ``supplying_store_id``, ``requested_qty``, ``approved_qty``,
        ``status``, ``notes``, ``rejection_reason``, ``created_by``,
        ``approved_by``, ``created_at``, and ``updated_at``, or ``None``
        if the request does not exist.
    """
    from app.inventory.repository import get_transfer_request_by_id

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        request = await get_transfer_request_by_id(session, UUID(request_id))
        if not request or str(request.tenant_id) != tenant_id:
            return None
        return {
            "id": str(request.id),
            "product_id": str(request.product_id),
            "requesting_store_id": str(request.requesting_store_id),
            "supplying_store_id": str(request.supplying_store_id),
            "requested_qty": float(request.requested_qty),
            "approved_qty": float(request.approved_qty) if request.approved_qty else None,
            "status": request.status,
            "notes": request.notes,
            "rejection_reason": request.rejection_reason,
            "created_by": str(request.created_by) if request.created_by else None,
            "approved_by": str(request.approved_by) if request.approved_by else None,
            "created_at": request.created_at.isoformat() if request.created_at else None,
            "updated_at": request.updated_at.isoformat() if request.updated_at else None,
        }


async def get_main_store_dashboard(tenant_id: str) -> dict:
    """Retrieve dashboard data for the tenant's main warehouse store.

    Fetches the main (warehouse) store details along with stock levels
    across all stores in the tenant. Used for the warehouse management
    dashboard view.

    Args:
        tenant_id: Unique identifier of the tenant.

    Returns:
        A dictionary containing ``main_store`` (details of the warehouse
        store or ``None``) and ``stores`` (list of stores with their stock
        summaries).
    """
    from app.inventory.repository import get_stores_with_stock
    from app.stores.repository import get_main_store

    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        main_store = await get_main_store(session, UUID(tenant_id))
        stores = await get_stores_with_stock(session, UUID(tenant_id))
        return {
            "main_store": {
                "id": str(main_store.id),
                "name": main_store.name,
                "address": main_store.address,
                "is_warehouse": main_store.is_warehouse,
            }
            if main_store
            else None,
            "stores": stores,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  PLATFORM / FLUTTERWAVE SUBACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════


async def get_subaccount(tenant_id: str) -> dict | None:
    """Retrieve the Flutterwave subaccount associated with a tenant.

    Looks up the payment subaccount record for the given tenant from the
    payments database.

    Args:
        tenant_id: Unique identifier of the tenant whose subaccount is
            being queried.

    Returns:
        A dictionary containing ``subaccount_code``, ``account_number``,
        ``bank_code``, ``bank_name``, ``business_name``, and
        ``percentage_charge``, or ``None`` if no subaccount exists.
    """
    from app.payments.repository import get_subaccount_by_tenant

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sub = await get_subaccount_by_tenant(session, UUID(tenant_id))
        if not sub:
            return None
        return {
            "subaccount_code": sub.subaccount_code,
            "account_number": sub.account_number,
            "bank_code": sub.bank_code,
            "bank_name": sub.bank_name,
            "business_name": sub.business_name,
            "percentage_charge": float(sub.percentage_charge),
        }


async def get_subaccount_by_code(*, subaccount_code: str) -> dict | None:
    """Retrieve a subaccount by its Flutterwave subaccount code.

    Looks up a payment subaccount record using the Flutterwave-assigned
    subaccount code rather than the tenant identifier.

    Args:
        subaccount_code: The Flutterwave-assigned subaccount code to look
            up.

    Returns:
        A dictionary containing ``id``, ``tenant_id``, ``subaccount_code``,
        ``account_number``, ``bank_code``, ``bank_name``, ``business_name``,
        and ``percentage_charge``, or ``None`` if no matching subaccount
        exists.
    """
    from app.payments.repository import get_subaccount_by_code as repo_get

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sub = await repo_get(session, subaccount_code)
        if not sub:
            return None
        return {
            "id": str(sub.id),
            "tenant_id": str(sub.tenant_id),
            "subaccount_code": sub.subaccount_code,
            "account_number": sub.account_number,
            "bank_code": sub.bank_code,
            "bank_name": sub.bank_name,
            "business_name": sub.business_name,
            "percentage_charge": float(sub.percentage_charge),
        }


async def list_all_subaccounts() -> list:
    """List all Flutterwave subaccounts across all tenants.

    Retrieves every subaccount record from the payments database, returning
    them as a flat list of dictionaries.

    Returns:
        A list of subaccount dictionaries, each containing ``id``,
        ``tenant_id``, ``subaccount_code``, ``account_number``,
        ``bank_code``, ``bank_name``, ``business_name``, and
        ``percentage_charge``.
    """
    from app.payments.repository import list_subaccounts as repo_list

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        subs = await repo_list(session)
        return [
            {
                "id": str(s.id),
                "tenant_id": str(s.tenant_id),
                "subaccount_code": s.subaccount_code,
                "account_number": s.account_number,
                "bank_code": s.bank_code,
                "bank_name": s.bank_name,
                "business_name": s.business_name,
                "percentage_charge": float(s.percentage_charge),
            }
            for s in subs
        ]


async def create_subaccount_on_flutterwave(
    *,
    tenant_id: str,
    account_bank: str,
    account_number: str,
    business_name: str,
    business_mobile: str,
    business_email: str | None = None,
    business_contact: str | None = None,
    split_value: float = 0.5,
    split_type: str = "percentage",
) -> dict:
    """Create a new subaccount on Flutterwave and persist it locally.

    Calls the Flutterwave API to create a payment subaccount for the tenant,
    then upserts the resulting subaccount details into the local payments
    database for future payment splits.

    Args:
        tenant_id: Unique identifier of the tenant that will own this
            subaccount.
        account_bank: Bank code of the subaccount (e.g. "044" for Access
            Bank).
        account_number: Bank account number for the subaccount.
        business_name: Registered business name for the subaccount.
        business_mobile: Business mobile phone number.
        business_email: Optional business email address.
        business_contact: Optional contact person for the business.
        split_value: Proportion of each transaction to route to this
            subaccount (default 0.5).
        split_type: Type of split calculation — "percentage" or "flat".

    Returns:
        The raw Flutterwave API response dictionary containing
        ``subaccount_id``, ``account_number``, ``account_bank``, and
        ``full_name``.

    Raises:
        ValueError: If the Flutterwave API call fails or the response
            does not contain a ``subaccount_id``.
    """

    fw_result = await flutterwave_service.create_subaccount(
        account_bank=account_bank,
        account_number=account_number,
        business_name=business_name,
        business_mobile=business_mobile,
        business_email=business_email,
        business_contact=business_contact,
        split_value=split_value,
        split_type=split_type,
    )
    if not fw_result or "subaccount_id" not in fw_result:
        raise ValueError("flutterwave_subaccount_create_failed")

    await upsert_subaccount(
        tenant_id=tenant_id,
        subaccount_code=fw_result["subaccount_id"],
        account_number=fw_result["account_number"],
        bank_code=fw_result["account_bank"],
        bank_name=fw_result.get("bank_name", ""),
        business_name=fw_result["full_name"],
        percentage_charge=Decimal(str(split_value)),
        raw_response=fw_result,
    )
    return fw_result


async def update_subaccount_on_flutterwave(
    *,
    tenant_id: str,
    account_bank: str | None = None,
    account_number: str | None = None,
    business_name: str | None = None,
    business_mobile: str | None = None,
    business_email: str | None = None,
    split_value: float | None = None,
    split_type: str | None = None,
) -> dict:
    """Update an existing Flutterwave subaccount and sync changes locally.

    Retrieves the tenant's current subaccount, calls the Flutterwave API
    to update it with the provided fields, then persists the updated
    details to the local payments database.

    Args:
        tenant_id: Unique identifier of the tenant whose subaccount is
            being updated.
        account_bank: Optional new bank code.
        account_number: Optional new bank account number.
        business_name: Optional new business name.
        business_mobile: Optional new business mobile number.
        business_email: Optional new business email.
        split_value: Optional new split proportion.
        split_type: Optional new split type.

    Returns:
        The raw Flutterwave API response dictionary with the updated
        subaccount details.

    Raises:
        ValueError: If no subaccount exists for the tenant.
    """
    # from api.services import flutterwave_service

    existing = await get_subaccount(tenant_id=tenant_id)
    if not existing:
        raise ValueError("subaccount_not_found")

    fw_result = await flutterwave_service.update_subaccount(
        subaccount_code=existing["subaccount_code"],
        account_bank=account_bank,
        account_number=account_number,
        business_name=business_name,
        business_mobile=business_mobile,
        business_email=business_email,
        split_value=split_value,
        split_type=split_type,
    )

    await update_subaccount(
        tenant_id=tenant_id,
        subaccount_code=existing["subaccount_code"],
        account_number=fw_result.get("account_number"),
        bank_code=fw_result.get("account_bank"),
        bank_name=fw_result.get("bank_name"),
        business_name=fw_result.get("full_name"),
        percentage_charge=fw_result.get("split_value"),
    )
    return fw_result


async def delete_subaccount_on_flutterwave(*, tenant_id: str) -> dict:
    """Delete a Flutterwave subaccount and deactivate it locally.

    Retrieves the tenant's current subaccount, calls the Flutterwave API
    to delete it, then marks the local record as inactive.

    Args:
        tenant_id: Unique identifier of the tenant whose subaccount is
            being deleted.

    Returns:
        A dictionary containing ``status: "deleted"``.

    Raises:
        ValueError: If no subaccount exists for the tenant.
    """

    existing = await get_subaccount(tenant_id=tenant_id)
    if not existing:
        raise ValueError("subaccount_not_found")

    await flutterwave_service.delete_subaccount(subaccount_code=existing["subaccount_code"])
    await delete_subaccount(tenant_id=tenant_id)
    return {"status": "deleted"}


async def update_subaccount(
    *,
    tenant_id: str,
    subaccount_code: str,
    account_number: str | None = None,
    bank_code: str | None = None,
    bank_name: str | None = None,
    business_name: str | None = None,
    percentage_charge: float | None = None,
) -> dict:
    """Update the local subaccount record for a tenant.

    Directly modifies the subaccount fields in the payments database
    without calling the Flutterwave API. Use this when you only need to
    update local metadata.

    Args:
        tenant_id: Unique identifier of the tenant.
        subaccount_code: Flutterwave subaccount code (used for lookup
            validation).
        account_number: Optional new bank account number.
        bank_code: Optional new bank code.
        bank_name: Optional new bank name.
        business_name: Optional new business name.
        percentage_charge: Optional new percentage charge value.

    Returns:
        A dictionary containing the updated subaccount details including
        ``id``, ``subaccount_code``, ``account_number``, ``bank_code``,
        ``bank_name``, ``business_name``, and ``percentage_charge``.

    Raises:
        ValueError: If no subaccount exists for the tenant.
    """
    from app.payments.repository import get_subaccount_by_tenant

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sub = await get_subaccount_by_tenant(session, UUID(tenant_id))
        if not sub:
            raise ValueError("Subaccount not found")
        if account_number is not None:
            sub.account_number = account_number
        if bank_code is not None:
            sub.bank_code = bank_code
        if bank_name is not None:
            sub.bank_name = bank_name
        if business_name is not None:
            sub.business_name = business_name
        if percentage_charge is not None:
            sub.percentage_charge = percentage_charge
        await session.commit()
        return {
            "id": str(sub.id),
            "subaccount_code": sub.subaccount_code,
            "account_number": sub.account_number,
            "bank_code": sub.bank_code,
            "bank_name": sub.bank_name,
            "business_name": sub.business_name,
            "percentage_charge": float(sub.percentage_charge),
        }


async def delete_subaccount(*, tenant_id: str) -> dict:
    """Soft-delete a subaccount by marking it as inactive.

    Sets the ``is_active`` flag to ``False`` on the tenant's subaccount
    record. The record is not physically removed.

    Args:
        tenant_id: Unique identifier of the tenant.

    Returns:
        A dictionary containing ``subaccount_code`` and
        ``status: "deleted"``.

    Raises:
        ValueError: If no subaccount exists for the tenant.
    """
    from app.payments.repository import get_subaccount_by_tenant

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sub = await get_subaccount_by_tenant(session, UUID(tenant_id))
        if not sub:
            raise ValueError("Subaccount not found")
        sub.is_active = False
        await session.commit()
        return {"subaccount_code": sub.subaccount_code, "status": "deleted"}


async def upsert_subaccount(
    tenant_id: str,
    subaccount_code: str,
    account_number: str,
    bank_code: str,
    bank_name: str,
    business_name: str,
    percentage_charge: float = 1.5,
    raw_response: dict | None = None,
) -> dict:
    """Create or update a subaccount record in the local payments database.

    Constructs a ``Subaccount`` model and delegates to the repository
    upsert function. If a subaccount with the same code already exists,
    its fields are updated; otherwise a new record is inserted.

    Args:
        tenant_id: Unique identifier of the tenant that owns the
            subaccount.
        subaccount_code: Flutterwave-assigned subaccount identifier.
        account_number: Bank account number for the subaccount.
        bank_code: Bank code (e.g. "044" for Access Bank).
        bank_name: Human-readable bank name.
        business_name: Registered business name.
        percentage_charge: Proportion of each transaction routed to this
            subaccount (default 1.5).
        raw_response: Optional raw API response dictionary from Flutterwave
            to store for debugging.

    Returns:
        A dictionary containing ``id``, ``subaccount_code``,
        ``account_number``, ``bank_code``, ``bank_name``, and
        ``business_name``.
    """
    from app.payments.models import Subaccount
    from app.payments.repository import upsert_subaccount as repo_upsert_subaccount

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sub = Subaccount(
            tenant_id=UUID(tenant_id),
            subaccount_code=subaccount_code,
            account_number=account_number,
            bank_code=bank_code,
            bank_name=bank_name,
            business_name=business_name,
            percentage_charge=percentage_charge,
            raw_response=raw_response,
        )
        result = await repo_upsert_subaccount(session, sub)
        await session.commit()
        return {
            "id": str(result.id),
            "subaccount_code": result.subaccount_code,
            "account_number": result.account_number,
            "bank_code": result.bank_code,
            "bank_name": result.bank_name,
            "business_name": result.business_name,
        }


async def get_dva(tenant_id: str) -> dict | None:
    """Retrieve the dedicated virtual account (DVA) for a tenant.

    Looks up the virtual bank account assigned to the tenant from the
    payments database.

    Args:
        tenant_id: Unique identifier of the tenant whose DVA is being
            queried.

    Returns:
        A dictionary containing ``account_number``, ``account_name``,
        ``bank_name``, and ``customer_code``, or ``None`` if no DVA
        exists for the tenant.
    """
    from app.payments.repository import get_dva_by_tenant

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        dva = await get_dva_by_tenant(session, UUID(tenant_id))
        if not dva:
            return None
        return {
            "account_number": dva.account_number,
            "account_name": dva.account_name,
            "bank_name": dva.bank_name,
            "customer_code": dva.customer_code,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  DEDICATED VIRTUAL ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════


async def upsert_dva(
    tenant_id: str,
    customer_email: str,
    customer_name: str,
    customer_code: str,
    account_number: str,
    account_name: str,
    bank_name: str,
    bank_code: str,
    dva_id: str | None = None,
    raw_response: dict | None = None,
) -> dict:
    """Create or update a dedicated virtual account (DVA) for a tenant.

    Constructs a ``DedicatedVirtualAccount`` model and delegates to the
    repository upsert function. If a DVA with the same account number
    already exists, its fields are updated; otherwise a new record is
    inserted.

    Args:
        tenant_id: Unique identifier of the tenant that owns the DVA.
        customer_email: Email address associated with the DVA customer.
        customer_name: Name of the customer on the DVA.
        customer_code: Customer code for the DVA.
        account_number: Virtual bank account number assigned.
        account_name: Display name on the virtual bank account.
        bank_name: Name of the bank issuing the virtual account.
        bank_code: Bank code for the issuing bank.
        dva_id: Optional DVA identifier.
        raw_response: Optional raw API response dictionary for debugging.

    Returns:
        A dictionary containing ``id``, ``account_number``, ``account_name``,
        and ``bank_name``.
    """
    from app.payments.models import DedicatedVirtualAccount
    from app.payments.repository import upsert_dva as repo_upsert_dva

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        dva = DedicatedVirtualAccount(
            tenant_id=UUID(tenant_id),
            scope="business",
            customer_email=customer_email,
            customer_name=customer_name,
            customer_code=customer_code,
            account_number=account_number,
            account_name=account_name,
            bank_name=bank_name,
            bank_code=bank_code,
            dva_id=dva_id,
            raw_response=raw_response,
        )
        result = await repo_upsert_dva(session, dva)
        await session.commit()
        return {
            "id": str(result.id),
            "account_number": result.account_number,
            "account_name": result.account_name,
            "bank_name": result.bank_name,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENT INTENTS
# ═══════════════════════════════════════════════════════════════════════════════


async def confirm_payment_intent(reference: str, gateway_data: dict) -> dict:
    """Confirm a pending payment intent after successful gateway processing.

    Looks up the payment intent by its gateway reference, updates its
    status to "completed" with the gateway response data, and returns
    the confirmed payment details.

    Args:
        reference: The gateway transaction reference used to locate the
            payment intent.
        gateway_data: Response data from the payment gateway containing
            transaction details.

    Returns:
        A dictionary containing ``intent_id``, ``status``, ``sale_id``,
        ``amount``, and ``method``.

    Raises:
        ValueError: If no payment intent matches the given reference.
    """
    from app.payments.repository import get_intent_by_reference, update_intent_status

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        intent = await get_intent_by_reference(session, reference)
        if not intent:
            raise ValueError(f"payment_intent_not_found:{reference}")
        await update_intent_status(
            session, intent_id=intent.id, status="completed", gateway_data=gateway_data
        )
        await session.commit()
        return {
            "intent_id": str(intent.id),
            "status": "completed",
            "sale_id": str(intent.sale_id),
            "amount": float(intent.amount),
            "method": intent.method,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  TENANT LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════


async def get_tenant_by_id(tenant_id: str) -> dict | None:
    """Retrieve a tenant's basic details by its identifier.

    Fetches the tenant record from the tenancy database.

    Args:
        tenant_id: Unique identifier of the tenant to retrieve.

    Returns:
        A dictionary containing ``id``, ``slug``, ``business_name``,
        ``tier``, and ``status``, or ``None`` if the tenant does not
        exist.
    """
    from app.tenancy.repository import get_tenant_by_id

    sdb = _get_sdb("tenancy")
    async with sdb.session() as session:
        tenant = await get_tenant_by_id(session, UUID(tenant_id))
        if not tenant:
            return None
        return {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "business_name": tenant.business_name,
            "tier": tenant.tier,
            "status": tenant.status,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  PRODUCT QUERIES
# ═══════════════════════════════════════════════════════════════════════════════


async def list_products(
    tenant_id: str,
    search: str | None = None,
    category_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List products in a tenant's catalog with optional filtering.

    Retrieves a paginated list of products from the catalog database,
    with optional text search, category filtering, and status filtering.

    Args:
        tenant_id: Unique identifier of the tenant whose products are
            being listed.
        search: Optional search term to filter products by name.
        category_id: Optional category identifier to filter by.
        status: Optional status filter (e.g. "active", "deleted").
        page: Page number for pagination (1-indexed).
        page_size: Number of items per page.

    Returns:
        A dictionary containing ``items`` (list of product dictionaries),
        ``total`` count, ``page``, and ``page_size``.
    """
    from app.catalog.repository import list_products

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        items = await list_products(
            session,
            tenant_id=UUID(tenant_id),
            search=search,
            category_id=UUID(category_id) if category_id else None,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "items": [
                {
                    "id": str(p.id),
                    "public_id": p.public_id,
                    "name": p.name,
                    "sku": p.sku,
                    "selling_price": float(p.selling_price),
                    "category_id": str(p.category_id) if p.category_id else None,
                    "status": p.status,
                    "qr_url": p.qr_url,
                    "qr_payload": p.qr_payload,
                }
                for p in items
            ],
            "total": len(items),
            "page": page,
            "page_size": page_size,
        }


async def get_product_by_id(tenant_id: str, product_id: str) -> dict | None:
    """Retrieve a specific product from the catalog by its identifier.

    Fetches the product from the catalog database and validates that it
    belongs to the specified tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the product.
        product_id: Unique identifier of the product to retrieve.

    Returns:
        A dictionary containing the product details including ``id``,
        ``public_id``, ``name``, ``sku``, ``selling_price``,
        ``category_id``, ``status``, ``qr_url``, and ``qr_payload``, or
        ``None`` if the product does not exist.
    """
    from app.catalog.repository import get_product_by_id

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        product = await get_product_by_id(session, UUID(product_id))
        if not product or str(product.tenant_id) != tenant_id:
            return None
        return {
            "id": str(product.id),
            "public_id": product.public_id,
            "name": product.name,
            "sku": product.sku,
            "selling_price": float(product.selling_price),
            "category_id": str(product.category_id) if product.category_id else None,
            "status": product.status,
            "qr_url": product.qr_url,
            "qr_payload": product.qr_payload,
        }


SIZE_PRESETS = {"small": 6, "medium": 10, "large": 20}


async def get_product_qr_download(
    tenant_id: str, product_id: str, box_size: int = 20
) -> str | tuple[bytes, str] | None:
    """Retrieve the QR code for a product, either as a URL or generated PNG bytes.

    When box_size matches the stored image (20), returns the Cloudinary URL.
    For any other size, generates a fresh PNG from the qr_payload.

    Args:
        tenant_id: Unique identifier of the tenant that owns the product.
        product_id: Unique identifier of the product whose QR code is
            being requested.
        box_size: QR image box size (6=small, 10=medium, 20=large).

    Returns:
        A URL string, a tuple of ``(png_bytes, public_id)``, or ``None``.
    """
    from app.catalog.repository import get_product_by_id
    from app.catalog.qr import generate_qr_png

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        product = await get_product_by_id(session, UUID(product_id))
        if not product or str(product.tenant_id) != tenant_id:
            return None
        if box_size == 20 and product.qr_url and product.qr_url.startswith("http"):
            return product.qr_url
        if product.qr_payload:
            png_bytes = generate_qr_png(product.qr_payload, box_size=box_size, border=4)
            return png_bytes, product.public_id
        if product.qr_url and product.qr_url.startswith("http"):
            return product.qr_url
        return None


async def lookup_product_by_scan(store_id: str, product_id: str) -> dict | None:
    """Look up product details for a QR scan.

    Resolves the tenant from the store_id, fetches the catalog product,
    and overlays store-specific pricing if a store_product exists.

    Args:
        store_id: The store UUID from the QR payload.
        product_id: The product UUID from the QR payload.

    Returns:
        A dict with product details and store-specific pricing, or None.
    """
    from app.catalog.repository import get_product_by_id
    from app.stores.repository import get_store_by_id, get_store_product
    from app.inventory.models import StockBalance
    from sqlalchemy import select

    store_uid = UUID(store_id)
    product_uid = UUID(product_id)

    sdb_stores = _get_sdb("inventory")
    async with sdb_stores.session() as store_session:
        store = await get_store_by_id(store_session, store_uid)
        if not store:
            return None
        tenant_id = store.tenant_id

        store_product = await get_store_product(store_session, store_uid, product_uid)

        balance_q = (
            select(StockBalance.qty)
            .where(
                StockBalance.tenant_id == tenant_id,
                StockBalance.store_id == store_uid,
                StockBalance.product_id == product_uid,
            )
        )
        balance_result = await store_session.execute(balance_q)
        stock_qty = float(balance_result.scalar() or 0)

    sdb_catalog = _get_sdb("catalog")
    async with sdb_catalog.session() as cat_session:
        product = await get_product_by_id(cat_session, product_uid)
        if not product or product.tenant_id != tenant_id:
            return None

    result = {
        "product_id": str(product.id),
        "name": store_product.name if store_product else product.name,
        "sku": store_product.sku if store_product else product.sku,
        "selling_price": float(store_product.selling_price) if store_product else float(product.selling_price),
        "store_id": store_id,
        "store_name": store.name,
        "stock_qty": stock_qty,
    }
    return result


async def update_product(tenant_id: str, product_id: str, **kwargs) -> None:
    """Update a product's fields in the catalog.

    Delegates to the catalog repository to update the specified fields on
    the product record.

    Args:
        tenant_id: Unique identifier of the tenant that owns the product.
        product_id: Unique identifier of the product to update.
        **kwargs: Arbitrary keyword arguments representing the fields to
            update (e.g. ``name``, ``selling_price``, ``sku``,
            ``category_id``).
    """
    from app.catalog.repository import update_product

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        await update_product(session, UUID(product_id), **kwargs)
        await session.commit()


async def delete_product(tenant_id: str, product_id: str) -> None:
    """Soft-delete a product by setting its status to deleted.

    Marks the product as deleted in the catalog without physically removing
    the record.

    Args:
        tenant_id: Unique identifier of the tenant that owns the product.
        product_id: Unique identifier of the product to delete.
    """
    from app.catalog.repository import update_product

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        await update_product(session, UUID(product_id), status="deleted")
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
#  CATEGORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_category(
    tenant_id: str,
    store_id: str,
    name: str,
    description: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a new product category within a store.

    Plans and persists a new category through the catalog service.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Unique identifier of the store that owns the category.
        name: Display name of the category.
        description: Optional description of the category.
        actor_id: Optional identifier of the user performing this action.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created category details
        including ``id``, ``name``, ``description``, and ``created_at``.

    Raises:
        ValueError: If a category with the same name already exists within
            the store.
    """
    from app.catalog.repository import (
        create_category as repo_create_category,
        get_category_by_name,
    )
    from app.catalog.schemas import CategoryCreateCommand
    from app.catalog.service import plan_category_creation

    tenant_uid = UUID(tenant_id)
    store_uid = UUID(store_id)

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        existing = await get_category_by_name(session, store_uid, name)
        if existing:
            raise ValueError(f"Category '{name}' already exists")

    command = CategoryCreateCommand(
        store_id=store_uid,
        name=name,
        description=description,
        correlation_id=correlation_id,
    )

    result, category_model = plan_category_creation(command, tenant_uid)
    async with sdb.session() as session:
        await repo_create_category(session, category_model)
        await session.commit()

    return result.model_dump(mode="json")


@cached(prefix="categories:list", ttl=300, key_func=lambda store_id, **kw: store_id)
async def list_categories(store_id: str) -> list[dict]:
    """List all product categories belonging to a store.

    Retrieves all category records from the catalog database for the given
    store.

    Args:
        store_id: Unique identifier of the store whose categories are
            being listed.

    Returns:
        A list of category dictionaries, each containing ``id``, ``name``,
        ``description``, and ``created_at``.
    """
    from app.catalog.repository import list_categories

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        items = await list_categories(session, UUID(store_id))
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "description": c.description,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in items
        ]


async def get_category(store_id: str, category_id: str) -> dict | None:
    """Retrieve a specific product category by its identifier.

    Fetches the category from the catalog database and validates that it
    belongs to the specified store.

    Args:
        store_id: Unique identifier of the store that owns the category.
        category_id: Unique identifier of the category to retrieve.

    Returns:
        A dictionary containing the category details including ``id``,
        ``store_id``, ``name``, ``description``, and ``created_at``,
        or ``None`` if the category does not exist.
    """
    from app.catalog.repository import get_category_by_id

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        category = await get_category_by_id(session, UUID(category_id))
        if not category or str(category.store_id) != store_id:
            return None
        return {
            "id": str(category.id),
            "store_id": str(category.store_id),
            "name": category.name,
            "description": category.description,
            "created_at": category.created_at.isoformat() if category.created_at else None,
        }


async def update_category(store_id: str, category_id: str, **kwargs) -> None:
    """Update a category's fields in the catalog.

    Modifies the specified fields on a category record. Validates that
    the new name (if provided) does not conflict with another category
    in the same store, and that the parent category (if provided) exists.

    Args:
        store_id: Unique identifier of the store that owns the category.
        category_id: Unique identifier of the category to update.
        **kwargs: Arbitrary keyword arguments representing the fields to
            update (e.g. ``name``, ``description``, ``parent_id``).

    Raises:
        ValueError: If a category with the same name already exists, or
            the category is not found.
    """
    from app.catalog.repository import get_category_by_name, update_category

    store_uid = UUID(store_id)
    cat_uid = UUID(category_id)

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        if "name" in kwargs:
            dup = await get_category_by_name(session, store_uid, kwargs["name"])
            if dup and dup.id != cat_uid:
                raise ValueError(f"Category '{kwargs['name']}' already exists")

        existing = await update_category(session, cat_uid, **kwargs)
        if str(existing.store_id) != store_id:
            raise ValueError("category_not_found")
        await session.commit()


async def delete_category(store_id: str, category_id: str) -> None:
    """Delete a product category from the catalog.

    Removes the category record after verifying that no products are
    currently assigned to it.

    Args:
        store_id: Unique identifier of the store that owns the category.
        category_id: Unique identifier of the category to delete.

    Raises:
        ValueError: If the category still has products assigned to it.
    """
    from app.catalog.models import Product
    from app.catalog.repository import delete_category as repo_delete_category
    from sqlalchemy import select

    sdb = _get_sdb("catalog")
    async with sdb.session() as session:
        result = await session.execute(
            select(Product)
            .where(Product.category_id == UUID(category_id), Product.store_id == UUID(store_id))
            .limit(1)
        )
        if result.scalar_one_or_none():
            raise ValueError("category_has_products")
        await repo_delete_category(session, UUID(category_id))
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
#  CART OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


async def _get_or_create_cart(
    session: AsyncSession,
    tenant_id: str,
    session_id: str,
    store_id: str,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Retrieve an existing active cart or create a new one for a session.

    Internal helper that checks for an active, non-expired cart matching
    the session identifier. If found, returns the existing cart. Otherwise,
    creates a new cart with a 30-minute expiry window.

    Args:
        session: Active database session for the transaction.
        tenant_id: Unique identifier of the tenant that owns the cart.
        session_id: Unique session identifier linking the cart to a
            client-side checkout session.
        customer_name: Optional customer name for the cart.
        customer_phone: Optional customer phone number for the cart.
        created_by: Optional identifier of the user creating the cart.

    Returns:
        A dictionary containing ``id``, ``session_id``, ``status``, and
        ``resumed`` (``True`` if an existing cart was returned, ``False``
        if a new cart was created). New carts also include an internal
        ``_cart`` key with the model instance.
    """
    from datetime import UTC, datetime, timedelta

    from app.cart.models import Cart
    from app.cart.repository import get_cart_by_session

    existing = await get_cart_by_session(session, session_id)
    if existing and existing.status == "active" and existing.expires_at > datetime.now(UTC):
        return {
            "id": str(existing.id),
            "session_id": existing.session_id,
            "store_id": str(existing.store_id),
            "status": existing.status,
            "resumed": True,
        }

    cart_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=360)
    _random_string = generate_random_timestamp_string()
    cart_name = f'CART-{_random_string}'
    cart = Cart(
        id=cart_id,
        cart_name=cart_name,
        tenant_id=UUID(tenant_id),
        store_id=UUID(store_id),
        session_id=session_id,
        status="active",
        customer_name=customer_name,
        customer_phone=customer_phone,
        created_by=UUID(created_by) if created_by else None,
        expires_at=expires_at,
    )
    session.add(cart)
    return {
        "id": str(cart_id),
        "session_id": session_id,
        "store_id": store_id,
        "status": "active",
        "resumed": False,
        "_cart": cart,
    }


async def create_or_resume_cart(
    tenant_id: str,
    session_id: str,
    store_id: str,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    actor_id: str | None = None,
) -> dict:
    """Create a new shopping cart or resume an existing active one.

    First checks the tenant's pending fee balance to ensure it has not
    exceeded the maximum allowed. Then either resumes an existing active
    cart for the session or creates a new one with a 30-minute expiry.

    Args:
        tenant_id: Unique identifier of the tenant that owns the cart.
        session_id: Unique session identifier linking the cart to a
            client-side checkout session.
        customer_name: Optional customer name for the cart.
        customer_phone: Optional customer phone number for the cart.
        actor_id: Optional identifier of the user performing this action.

    Returns:
        A dictionary containing ``id``, ``session_id``, ``status``, and
        ``resumed`` (``True`` if an existing cart was returned, ``False``
        if a new cart was created).

    Raises:
        ValueError: If the tenant's pending fee balance has exceeded the
            maximum allowed threshold.
    """
    from app.platform.fee_calculator import get_max_pending_balance, get_pending_fee_balance

    sdb_payments = _get_sdb("payments")
    async with sdb_payments.session() as pay_session:
        pending_balance = await get_pending_fee_balance(pay_session, UUID(tenant_id))
        max_balance = await get_max_pending_balance(pay_session)
        if pending_balance >= max_balance:
            raise ValueError("fee_balance_exceeded")

    sdb = _get_sdb("cart")
    async with sdb.session() as session:
        cart_result = await _get_or_create_cart(
            session=session,
            tenant_id=tenant_id,
            session_id=session_id,
            store_id=store_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            created_by=actor_id,
        )
        if not cart_result.get("_cart"):
            return {
                "id": str(cart_result["id"]),
                "session_id": cart_result["session_id"],
                "store_id": cart_result["store_id"],
                "status": cart_result["status"],
                "resumed": True,
            }
        await session.commit()
        return {
            "id": str(cart_result["id"]),
            "session_id": cart_result["session_id"],
            "store_id": cart_result["store_id"],
            "status": cart_result["status"],
            "resumed": False,
        }


async def remove_cart_item(tenant_id: str, item_id: str, actor_id: str | None = None) -> None:
    """Remove an item from a shopping cart.

    Retrieves the cart item, plans the removal through the cart service
    (generating outbox events), then deletes the item record.

    Args:
        tenant_id: Unique identifier of the tenant that owns the cart.
        item_id: Unique identifier of the cart item to remove.
        actor_id: Optional identifier of the user performing this action.

    Raises:
        ValueError: If the cart item is not found.
    """
    from app.cart.repository import get_cart_item, remove_cart_item
    from app.cart.schemas import RemoveItemCommand
    from app.cart.service import plan_remove_item

    sdb = _get_sdb("cart")
    async with sdb.session() as session:
        item = await get_cart_item(session, UUID(item_id))
        if not item:
            raise ValueError("cart_item_not_found")

        command = RemoveItemCommand(
            cart_id=item.cart_id,
            tenant_id=UUID(tenant_id),
            item_id=UUID(item_id),
            created_by=UUID(actor_id) if actor_id else None,
        )
        outbox = plan_remove_item(command, item)
        await remove_cart_item(session, UUID(item_id))
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

        # Log direct deletion to tenant audit trail
        if actor_id:
            from app.identity.models import AuthAuditLog as _AuditLog

            sdb_identity = _get_sdb("identity")
            async with sdb_identity.session() as audit_session:
                audit_session.add(
                    _AuditLog(
                        user_id=UUID(actor_id),
                        tenant_id=UUID(tenant_id),
                        action="cart_item_deleted",
                        details={
                            "item_id": item_id,
                            "cart_id": str(item.cart_id),
                            "product_id": str(item.product_id),
                            "product_name": item.name,
                            "qty": item.qty,
                            "unit_price": item.unit_price,
                        },
                    )
                )
                await audit_session.commit()


async def void_cart_item(
    tenant_id: str,
    item_id: str,
    actor_id: str,
    supervisor_pin: str,
) -> None:
    """Remove a cart item with supervisor PIN override.

    Used when the actor (cashier) does not have cart:delete permission.
    The supervisor PIN must belong to a user with cart:delete in the same tenant
    and must not be expired (7-day TTL).

    Args:
        tenant_id: Unique identifier of the tenant.
        item_id: Unique identifier of the cart item to remove.
        actor_id: The user performing the action (cashier).
        supervisor_pin: 4-6 digit PIN of a supervisor with cart:delete permission.

    Raises:
        ValueError: If PIN is invalid/expired, supervisor not found, or item not found.
    """
    from datetime import UTC as _UTC, datetime as _datetime

    from app.core.security import verify_pin
    from app.cart.repository import get_cart_item, remove_cart_item
    from app.cart.schemas import RemoveItemCommand
    from app.cart.service import plan_remove_item
    from app.identity.models import (
        Permission,
        Role,
        RolePermission,
        SupervisorPin,
        User,
        UserRole,
    )

    tid = UUID(tenant_id)
    supervisor_id: UUID | None = None
    now = _datetime.now(_UTC)

    sdb_identity = _get_sdb("identity")
    async with sdb_identity.session() as identity_session:
        # Query supervisor_pins with expiry check, joined to users for tenant + status
        pins = (
            await identity_session.execute(
                select(SupervisorPin)
                .join(User, User.id == SupervisorPin.user_id)
                .where(
                    SupervisorPin.expires_at > now,
                    User.tenant_id == tid,
                    User.status == "active",
                )
            )
        ).scalars().all()

        for pin_record in pins:
            if verify_pin(supervisor_pin, pin_record.pin_hash):
                # Check if this user has cart:delete permission
                has_perm = (
                    await identity_session.execute(
                        select(Permission.id)
                        .join(RolePermission, RolePermission.permission_id == Permission.id)
                        .join(Role, Role.id == RolePermission.role_id)
                        .join(UserRole, UserRole.role_id == Role.id)
                        .where(
                            UserRole.user_id == pin_record.user_id,
                            Permission.name == "cart:delete",
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()

                if has_perm:
                    supervisor_id = pin_record.user_id
                    break

    if not supervisor_id:
        raise ValueError("invalid_supervisor_pin")

    # Remove the item with audit trail
    sdb = _get_sdb("cart")
    async with sdb.session() as session:
        item = await get_cart_item(session, UUID(item_id))
        if not item:
            raise ValueError("cart_item_not_found")

        command = RemoveItemCommand(
            cart_id=item.cart_id,
            tenant_id=tid,
            item_id=UUID(item_id),
            created_by=UUID(actor_id),
            approved_by=supervisor_id,
        )
        outbox = plan_remove_item(command, item)
        await remove_cart_item(session, UUID(item_id))
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    # Log to tenant audit trail
    sdb_identity = _get_sdb("identity")
    async with sdb_identity.session() as audit_session:
        from app.identity.models import AuthAuditLog as _AuditLog

        audit_session.add(
            _AuditLog(
                user_id=UUID(actor_id),
                tenant_id=tid,
                action="cart_void_approved",
                details={
                    "item_id": item_id,
                    "cart_id": str(item.cart_id),
                    "product_id": str(item.product_id),
                    "product_name": item.name,
                    "qty": item.qty,
                    "unit_price": item.unit_price,
                    "supervisor_id": str(supervisor_id),
                },
            )
        )
        await audit_session.commit()


async def get_cart(tenant_id: str, cart_id: str) -> dict:
    """Retrieve a shopping cart and its line items.

    Fetches the cart from the cart database along with all associated
    cart items.

    Args:
        tenant_id: Unique identifier of the tenant that owns the cart.
        cart_id: Unique identifier of the cart to retrieve.

    Returns:
        A dictionary containing ``id``, ``session_id``, ``status``,
        ``customer_name``, ``customer_phone``, ``expires_at``, and
        ``items`` (list of cart item dictionaries each with ``id``,
        ``product_id``, ``product_public_id``, ``name``, ``unit_price``,
        and ``qty``).

    Raises:
        ValueError: If the cart is not found.
    """
    from app.cart.repository import get_cart_by_id, get_cart_items

    sdb = _get_sdb("cart")
    async with sdb.session() as session:
        cart = await get_cart_by_id(session, UUID(cart_id))
        if not cart:
            raise ValueError("cart_not_found")
        items = await get_cart_items(session, UUID(cart_id))
        return {
            "id": str(cart.id),
            "session_id": cart.session_id,
            "status": cart.status,
            "customer_name": cart.customer_name,
            "customer_phone": cart.customer_phone,
            "expires_at": cart.expires_at.isoformat() if cart.expires_at else None,
            "items": [
                {
                    "id": str(i.id),
                    "product_id": str(i.product_id),
                    "product_public_id": i.product_public_id,
                    "name": i.name,
                    "unit_price": float(i.unit_price),
                    "qty": float(i.qty),
                }
                for i in items
            ],
        }


async def checkout_cart(
    tenant_id: str,
    cart_id: str,
    actor_id: str | None = None,
    items: list[dict] | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    store_id: str | None = None,
    coupon_code: str | None = None,
    discount_id: str | None = None,
) -> dict:
    """Check out a shopping cart and create the corresponding sale.

    Supports two flows: a new flow where items are provided inline from
    the client (scanned or selected at checkout time), and a legacy flow
    where items were previously persisted in the cart database.

    In both flows the function validates the fee balance, resolves product
    details from the catalog, creates a sale record with line items through
    the sales service, marks the cart as checked out, and records outbox
    events.

    Args:
        tenant_id: Unique identifier of the tenant that owns the cart.
        cart_id: Unique identifier of the cart to check out.
        actor_id: Optional identifier of the user performing the checkout
            (typically the cashier).
        items: Optional list of item dictionaries for the new flow, each
            containing ``product_public_id`` and ``qty``. When ``None``,
            the legacy flow uses items already in the cart.
        customer_name: Optional customer name to override the cart's stored
            value.
        customer_phone: Optional customer phone to override the cart's
            stored value.

    Returns:
        A dictionary containing the newly created sale details including
        ``id``, ``sale_number``, ``total``, ``amount_paid``, and ``status``.

    Raises:
        ValueError: If the cart is not found, the cart is empty, the fee
            balance is exceeded, or referenced products do not exist.
    """
    from app.cart.repository import (
        bulk_add_cart_items,
        get_cart_by_id,
        get_cart_items,
        update_cart_status,
    )
    from app.cart.schemas import CheckoutCommand, CheckoutItem
    from app.cart.service import plan_bulk_add_items, plan_checkout
    from app.catalog.repository import get_products_by_public_ids
    from app.sales.schemas import SaleCreateCommand, SaleItemLine
    from app.sales.service import plan_sale_creation
    from app.sales.repository import create_sale as repo_create_sale, create_sale_items
    from app.platform.fee_calculator import get_max_pending_balance, get_pending_fee_balance

    sdb_payments = _get_sdb("payments")
    async with sdb_payments.session() as pay_session:
        pending_balance = await get_pending_fee_balance(pay_session, UUID(tenant_id))
        max_balance = await get_max_pending_balance(pay_session)
        if pending_balance >= max_balance:
            raise ValueError("fee_balance_exceeded")

    cart_uid = UUID(cart_id)
    tenant_uid = UUID(tenant_id)
    actor_uid = UUID(actor_id) if actor_id else None

    sdb_cart = _get_sdb("cart")
    async with sdb_cart.session() as cart_session:
        cart = await get_cart_by_id(cart_session, cart_uid)
        if not cart:
            raise ValueError("cart_not_found")

        if items is not None:
            # ── New flow: items come from the client (scanned/selected) ──
            if not items:
                raise ValueError("cart_empty")

            validated = [
                CheckoutItem(product_public_id=i["product_public_id"], qty=Decimal(str(i["qty"])))
                for i in items
            ]

            # Batch-lookup all products from catalog (1 query)
            public_ids = [i.product_public_id for i in validated]
            sdb_catalog = _get_sdb("catalog")
            async with sdb_catalog.session() as cat_session:
                products = await get_products_by_public_ids(cat_session, public_ids)
                found = {p.public_id for p in products}
                missing = [pid for pid in public_ids if pid not in found]
                if missing:
                    raise ValueError(f"products_not_found:{','.join(missing)}")

            # Resolve prices from store_products if store_id is on the cart
            store_product_map = {}
            cart_store_id = getattr(cart, "store_id", None)
            if cart_store_id:
                from app.stores.repository import get_store_product

                sdb_inv = _get_sdb("inventory")
                async with sdb_inv.session() as inv_session:
                    for p in products:
                        sp = await get_store_product(
                            inv_session, cart_store_id, p.id
                        )
                        if sp:
                            store_product_map[p.public_id] = sp

            product_map = {p.public_id: p for p in products}
            resolved = []
            for i in validated:
                sp = store_product_map.get(i.product_public_id)
                price = float(sp.selling_price) if sp else float(
                    product_map[i.product_public_id].selling_price
                )
                resolved.append((
                    product_map[i.product_public_id].id,
                    i.product_public_id,
                    sp.name if sp else product_map[i.product_public_id].name,
                    Decimal(str(price)),
                    i.qty,
                    str(cart_store_id) if cart_store_id else None,
                ))

            checkout_command = CheckoutCommand(
                cart_id=cart_uid,
                tenant_id=tenant_uid,
                items=validated,
                customer_name=customer_name or cart.customer_name,
                customer_phone=customer_phone or cart.customer_phone,
                created_by=actor_uid,
            )

            # Plan cart items + outbox events
            cart_items, cart_item_outbox = plan_bulk_add_items(checkout_command, resolved)

            # Build sale items
            sale_items = [
                SaleItemLine(
                    product_id=product_map[i.product_public_id].id,
                    product_name=product_map[i.product_public_id].name,
                    qty=i.qty,
                    unit_price=Decimal(str(product_map[i.product_public_id].selling_price)),
                )
                for i in validated
            ]

            # Calculate subtotal
            subtotal = sum(
                float(si.unit_price) * float(si.qty) for si in sale_items
            )

            # Resolve discount
            discount_amount = Decimal("0")
            applied_coupon_code = None

            if coupon_code:
                coupon_result = await validate_coupon(
                    tenant_id=tenant_id, code=coupon_code, cart_subtotal=subtotal
                )
                if coupon_result.get("valid"):
                    discount_amount = Decimal(str(coupon_result["discount_amount"]))
                    applied_coupon_code = coupon_result.get("code")
                    # Increment usage
                    if coupon_result.get("coupon_id"):
                        await increment_coupon_usage(tenant_id, coupon_result["coupon_id"])
                else:
                    raise ValueError(f"invalid_coupon:{coupon_result.get('message', 'Unknown error')}")

            elif discount_id:
                disc = await get_discount(tenant_id=tenant_id, discount_id=discount_id)
                if disc and disc.get("is_active"):
                    disc_amount = await apply_discount(
                        subtotal=subtotal,
                        discount_type=disc["discount_type"],
                        value=disc["value"],
                        buy_x_get_y_free_qty=disc.get("buy_x_get_y_free_qty", 0),
                        cart_items=[{"qty": float(si.qty), "unit_price": float(si.unit_price)} for si in sale_items],
                    )
                    discount_amount = Decimal(str(disc_amount))

            sale_command = SaleCreateCommand(
                tenant_id=tenant_uid,
                cashier_id=actor_uid or cart_uid,
                store_id=UUID(store_id) if store_id else (UUID(str(cart_store_id)) if cart_store_id else UUID(cart_id)),
                items=sale_items,
                customer_name=customer_name or cart.customer_name,
                customer_phone=customer_phone or cart.customer_phone,
                discount=discount_amount,
            )

            # Create sale in sales DB (separate session)
            sdb_sales = _get_sdb("sales")
            async with sdb_sales.session() as sales_session:
                result, sale_model, sale_item_models, sale_outbox = plan_sale_creation(sale_command)
                await repo_create_sale(sales_session, sale_model)
                await create_sale_items(sales_session, sale_item_models)
                for write in sale_outbox:
                    sales_session.add(write.to_model())
                await sales_session.commit()

            # Bulk-insert cart items + checkout in one cart transaction
            if cart_items:
                await bulk_add_cart_items(cart_session, cart_items)
            cart_checkout_outbox = plan_checkout(checkout_command, cart, cart_items)
            await update_cart_status(cart_session, cart_uid, "checked_out")
            for write in [*cart_item_outbox, *cart_checkout_outbox]:
                cart_session.add(write.to_model())
            await cart_session.commit()

            result_dict = result.model_dump(mode="json")
            result_dict["subtotal"] = round(subtotal, 2)
            result_dict["discount"] = round(float(discount_amount), 2)
            result_dict["coupon_code"] = applied_coupon_code
            return result_dict

        # ── Legacy flow: items already persisted in cart DB ──
        existing_items = await get_cart_items(cart_session, cart_uid)
        if not existing_items:
            raise ValueError("cart_empty")

        sale_items = [
            SaleItemLine(
                product_id=i.product_id,
                product_name=i.name,
                qty=Decimal(str(i.qty)),
                unit_price=Decimal(str(i.unit_price)),
            )
            for i in existing_items
        ]

        # Calculate subtotal
        subtotal = sum(float(si.unit_price) * float(si.qty) for si in sale_items)

        # Resolve discount
        discount_amount = Decimal("0")
        applied_coupon_code = None

        if coupon_code:
            coupon_result = await validate_coupon(
                tenant_id=tenant_id, code=coupon_code, cart_subtotal=subtotal
            )
            if coupon_result.get("valid"):
                discount_amount = Decimal(str(coupon_result["discount_amount"]))
                applied_coupon_code = coupon_result.get("code")
                if coupon_result.get("coupon_id"):
                    await increment_coupon_usage(tenant_id, coupon_result["coupon_id"])
            else:
                raise ValueError(f"invalid_coupon:{coupon_result.get('message', 'Unknown error')}")

        elif discount_id:
            disc = await get_discount(tenant_id=tenant_id, discount_id=discount_id)
            if disc and disc.get("is_active"):
                disc_amount = await apply_discount(
                    subtotal=subtotal,
                    discount_type=disc["discount_type"],
                    value=disc["value"],
                    buy_x_get_y_free_qty=disc.get("buy_x_get_y_free_qty", 0),
                    cart_items=[{"qty": float(si.qty), "unit_price": float(si.unit_price)} for si in sale_items],
                )
                discount_amount = Decimal(str(disc_amount))

        checkout_command = CheckoutCommand(
            cart_id=cart_uid,
            tenant_id=tenant_uid,
            created_by=actor_uid,
        )
        cart_outbox = plan_checkout(checkout_command, cart, existing_items)

        sale_command = SaleCreateCommand(
            tenant_id=tenant_uid,
            cashier_id=actor_uid or cart_uid,
            store_id=UUID(store_id) if store_id else UUID(str(getattr(cart, "store_id", None) or cart_id)),
            items=sale_items,
            customer_name=cart.customer_name,
            customer_phone=cart.customer_phone,
            discount=discount_amount,
        )

    from app.sales.service import plan_sale_creation
    from app.sales.repository import create_sale as repo_create_sale, create_sale_items

    sdb_sales = _get_sdb("sales")
    async with sdb_sales.session() as sales_session:
        result, sale_model, sale_item_models, outbox = plan_sale_creation(sale_command)
        await repo_create_sale(sales_session, sale_model)
        await create_sale_items(sales_session, sale_item_models)
        for write in outbox:
            sales_session.add(write.to_model())
        await sales_session.commit()

    async with sdb_cart.session() as cart_session:
        await update_cart_status(cart_session, cart_uid, "checked_out")
        for write in cart_outbox:
            cart_session.add(write.to_model())
        await cart_session.commit()

    result_dict = result.model_dump(mode="json")
    result_dict["subtotal"] = round(subtotal, 2)
    result_dict["discount"] = round(float(discount_amount), 2)
    result_dict["coupon_code"] = applied_coupon_code
    return result_dict


async def get_receipt_by_sale(business_id: str, sale_id: str) -> dict | None:
    """Retrieve the receipt associated with a completed sale.

    Fetches the receipt record linked to the specified sale from the sales
    database.

    Args:
        business_id: Unique identifier of the tenant that owns the sale.
        sale_id: Unique identifier of the sale whose receipt is being
            queried.

    Returns:
        A dictionary containing ``id``, ``sale_id``, ``receipt_number``,
        ``pdf_url``, ``sent_via``, and ``created_at``, or ``None`` if
        no receipt exists for the sale.
    """
    from app.sales.repository import get_receipt_by_sale as _get_receipt
    from app.sales.models import Sale

    sdb = _get_sdb("sales")
    async with sdb.session() as session:
        sale = await session.get(Sale, UUID(sale_id))
        if not sale or str(sale.tenant_id) != business_id:
            return None
        receipt = await _get_receipt(session, UUID(sale_id))
        if not receipt:
            return None
        return {
            "id": str(receipt.id),
            "sale_id": str(receipt.sale_id),
            "receipt_number": receipt.receipt_number,
            "pdf_url": receipt.pdf_url,
            "sent_via": receipt.sent_via,
            "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENT RECORDING
# ═══════════════════════════════════════════════════════════════════════════════


async def record_payment(
    business_id: str,
    sale_id: str,
    method: str,
    amount: Decimal,
    reference: str | None = None,
    gateway_resp: dict | None = None,
) -> dict:
    """Record a payment against a sale and update the sale status.

    Creates a payment record in the payments database and updates the
    sale's ``amount_paid`` and status. When the sale becomes fully paid,
    the platform fee is calculated and, for cash payments, a fee ledger
    entry is created. Outbox events are recorded for downstream processing.

    Args:
        business_id: Unique identifier of the tenant that owns the sale.
        sale_id: Unique identifier of the sale to record payment against.
        method: Payment method (e.g. "cash", "card", "transfer").
        amount: Amount being paid.
        reference: Optional payment reference from the gateway.
        gateway_resp: Optional raw gateway response dictionary for audit.

    Returns:
        A dictionary containing ``payment_id``, ``sale_id``, ``method``,
        ``amount``, ``payment_status``, ``sale_status``, ``total``,
        ``amount_paid``, and ``balance`` (remaining amount owed).

    Raises:
        ValueError: If the sale is not found.
    """
    from app.payments.models import Payment
    from app.payments.repository import create_payment
    from app.payments.service import plan_payment_success
    from app.sales.repository import get_sale_by_id

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        payment_id = uuid4()
        payment = Payment(
            id=payment_id,
            tenant_id=UUID(business_id),
            sale_id=UUID(sale_id),
            method=method,
            amount=float(amount),
            currency="NGN",
            status="completed",
            reference=reference,
            gateway_reference=reference,
            gateway_response=str(gateway_resp) if gateway_resp else None,
        )
        await create_payment(session, payment)

        sale = await get_sale_by_id(session, UUID(sale_id))
        fee_result = None
        if sale:
            sale.amount_paid = float(sale.amount_paid) + float(amount)
            sale_balance = float(sale.total) - float(sale.amount_paid)

            pm = dict(sale.payment_methods) if sale.payment_methods else {}
            pm["compare_total"] = float(sale.amount_paid)
            sale.payment_methods = pm

            if sale.amount_paid >= float(sale.total):
                sale.status = "completed"
            elif sale.amount_paid > 0:
                sale.status = "partial"

            if sale.status == "completed":
                from app.platform.fee_calculator import calculate_platform_fee

                fee_result = await calculate_platform_fee(session, float(sale.total))
                pm["platform_fee"] = fee_result["platform_fee"]
                pm["settlement_amount"] = fee_result["settlement_amount"]
                pm["fee_rule_id"] = fee_result["rule_id"]
                sale.payment_methods = pm

        else:
            sale_balance = float(amount)

        outbox = plan_payment_success(
            tenant_id=UUID(business_id),
            payment_id=payment_id,
            sale_id=sale_id,
            amount=str(amount),
            method=method,
            reference=reference,
            gateway_reference=reference,
        )
        for write in outbox:
            session.add(write.to_model())

        if (
            sale
            and sale.status == "completed"
            and method in ("cash",)
            and pm.get("platform_fee", 0) > 0
        ):
            from app.platform.models import PlatformFeeLedger

            total_fee = pm["platform_fee"]
            cash_fee_share = (
                total_fee * (float(amount) / float(sale.total))
                if float(sale.total) > 0
                else total_fee
            )
            ledger = PlatformFeeLedger(
                tenant_id=UUID(business_id),
                sale_id=UUID(sale_id),
                amount=round(cash_fee_share, 2),
                fee_type=fee_result["fee_type"],
                rate=fee_result["rate"],
                payment_method=method,
                status="pending",
            )
            session.add(ledger)

        await session.commit()
        return {
            "payment_id": str(payment_id),
            "sale_id": sale_id,
            "method": method,
            "amount": float(amount),
            "payment_status": "completed",
            "sale_status": sale.status if sale else "unknown",
            "total": float(sale.total) if sale else 0,
            "amount_paid": float(sale.amount_paid) if sale else float(amount),
            "balance": sale_balance,
        }


async def record_split_payment(
    business_id: str,
    sale_id: str,
    payments: list[dict],
) -> dict:
    """Record multiple payments against a single sale in a single batch.

    Creates payment records for each payment in the batch and updates the
    sale's ``amount_paid`` and status accordingly. When the sale becomes
    fully paid, the platform fee is calculated and fee ledger entries are
    created for cash payments.

    Args:
        business_id: Unique identifier of the tenant that owns the sale.
        sale_id: Unique identifier of the sale to record payments against.
        payments: List of payment dictionaries, each containing ``method``
            and ``amount``, and optionally ``reference``.

    Returns:
        A dictionary containing ``payments`` (list of created payment
        records), ``sale_id``, ``sale_status``, ``total``,
        ``amount_paid``, and ``balance``.

    Raises:
        ValueError: If the sale is not found.
    """
    from app.payments.models import Payment
    from app.payments.repository import create_payment
    from app.sales.repository import get_sale_by_id

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sale = await get_sale_by_id(session, UUID(sale_id))
        if not sale:
            raise ValueError("sale_not_found")

        total_batch = sum(Decimal(str(p["amount"])) for p in payments)

        results = []
        for p in payments:
            payment_id = uuid4()
            payment = Payment(
                id=payment_id,
                tenant_id=UUID(business_id),
                sale_id=UUID(sale_id),
                method=p["method"],
                amount=float(p["amount"]),
                currency="NGN",
                status="completed",
                reference=p.get("reference"),
                gateway_reference=p.get("reference"),
            )
            await create_payment(session, payment)
            results.append(
                {
                    "payment_id": str(payment_id),
                    "sale_id": sale_id,
                    "method": p["method"],
                    "amount": float(p["amount"]),
                    "status": "completed",
                }
            )

        sale.amount_paid = float(sale.amount_paid) + float(total_batch)

        pm = dict(sale.payment_methods) if sale.payment_methods else {}
        pm["compare_total"] = float(sale.amount_paid)
        sale.payment_methods = pm

        if sale.amount_paid >= float(sale.total):
            sale.status = "completed"
        elif sale.amount_paid > 0:
            sale.status = "partial"

        if sale.status == "completed":
            from app.platform.fee_calculator import calculate_platform_fee

            fee_result = await calculate_platform_fee(session, float(sale.total))
            pm["platform_fee"] = fee_result["platform_fee"]
            pm["settlement_amount"] = fee_result["settlement_amount"]
            pm["fee_rule_id"] = fee_result["rule_id"]
            sale.payment_methods = pm

            total_fee = fee_result["platform_fee"]
            if total_fee > 0:
                from app.platform.models import PlatformFeeLedger

                cash_payments = [p for p in payments if p["method"] == "cash"]
                for cp in cash_payments:
                    cash_fee_share = (
                        total_fee * (float(cp["amount"]) / float(sale.total))
                        if float(sale.total) > 0
                        else total_fee
                    )
                    ledger = PlatformFeeLedger(
                        tenant_id=UUID(business_id),
                        sale_id=UUID(sale_id),
                        amount=round(cash_fee_share, 2),
                        fee_type=fee_result["fee_type"],
                        rate=fee_result["rate"],
                        payment_method="cash",
                        status="pending",
                    )
                    session.add(ledger)

        await session.commit()
        return {
            "payments": results,
            "sale_id": sale_id,
            "sale_status": sale.status,
            "total": float(sale.total),
            "amount_paid": float(sale.amount_paid),
            "balance": float(sale.total) - float(sale.amount_paid),
        }


async def suggest_even_split(sale_id: str) -> dict:
    """Suggest an even three-way payment split for a sale.

    Calculates an equal division of the sale total across card, transfer,
    and cash payment methods. Useful as a default suggestion when a
    customer requests a split payment.

    Args:
        sale_id: Unique identifier of the sale to split.

    Returns:
        A dictionary containing ``sale_id``, ``total``, and ``splits``
        (a dictionary with ``card``, ``transfer``, and ``cash`` keys, each
        set to one-third of the total).

    Raises:
        ValueError: If the sale is not found.
    """
    from app.sales.repository import get_sale_by_id

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sale = await get_sale_by_id(session, UUID(sale_id))
        if not sale:
            raise ValueError("sale_not_found")

        total = float(sale.total)
        each = round(total / 3, 2)
        return {
            "sale_id": sale_id,
            "total": total,
            "splits": {"card": each, "transfer": each, "cash": each},
        }


async def _build_subaccounts_for_payment(
    session, tenant_id: UUID, sale_total: float
) -> list[dict] | None:
    """Build the subaccount split configuration for a payment gateway request.

    Internal helper that retrieves the tenant's subaccount and calculates
    the platform fee split parameters needed for Flutterwave payment
    requests. Returns ``None`` when no subaccount exists or the calculated
    platform fee is zero.

    Args:
        session: Active database session for the transaction.
        tenant_id: Unique identifier of the tenant.
        sale_total: Total amount of the sale used for fee calculation.

    Returns:
        A list containing a single subaccount dictionary with ``id``
        (the subaccount code), ``transaction_charge_type`` ("percentage"
        or "flat"), and ``transaction_charge`` (the fee rate), or ``None``
        if no split is applicable.
    """
    from app.payments.repository import get_subaccount_by_tenant
    from app.platform.fee_calculator import calculate_platform_fee

    sub = await get_subaccount_by_tenant(session, tenant_id)
    if not sub:
        return None

    fee_result = await calculate_platform_fee(session, sale_total)
    if fee_result["platform_fee"] <= 0:
        return None

    if fee_result["fee_type"] == "percentage":
        transaction_charge_type = "percentage"
        transaction_charge = fee_result["rate"] / 100
    else:
        transaction_charge_type = "flat"
        transaction_charge = fee_result["rate"]

    return [
        {
            "id": sub.subaccount_code,
            "transaction_charge_type": transaction_charge_type,
            "transaction_charge": transaction_charge,
        }
    ]


async def initiate_card_payment(
    *,
    business_id: str,
    sale_id: str,
    amount: Decimal,
    customer_email: str,
    customer_name: str | None = None,
) -> dict:
    """Initiate a card payment for a sale via Flutterwave.

    Creates a Flutterwave payment link for card transactions, generates a
    QR code for the payment URL, and persists a payment intent record.
    The payment link includes subaccount split configuration if the tenant
    has an active subaccount.

    Args:
        business_id: Unique identifier of the tenant processing the payment.
        sale_id: Unique identifier of the sale to collect payment for.
        amount: Amount to charge via card payment.
        customer_email: Email address of the customer for the payment link.
        customer_name: Optional name of the customer for the payment link.

    Returns:
        A dictionary containing ``sale_id``, ``payment_url``, ``qr_code_base64``,
        ``tx_ref``, ``amount``, and ``status: "pending"``.

    Raises:
        ValueError: If the sale is not found.
    """
    from app.sales.repository import get_sale_by_id
    from app.payments.models import PaymentIntent
    from app.payments.repository import create_intent
    from app.catalog.qr import generate_qr_base64

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sale = await get_sale_by_id(session, UUID(sale_id))
        if not sale:
            raise ValueError("sale_not_found")

        tx_ref = f"SF-{uuid4().hex[:16].upper()}"

        subaccounts = await _build_subaccounts_for_payment(
            session, UUID(business_id), float(sale.total)
        )

        card_result = await flutterwave_service.create_payment_link(
            amount=amount,
            tx_ref=tx_ref,
            customer_email=customer_email,
            customer_name=customer_name,
            payment_options="card",
            title="StoreFlow Card Payment",
            meta={"business_id": business_id, "sale_id": sale_id},
            subaccounts=subaccounts,
        )

        qr_base64 = generate_qr_base64(card_result["link"])

        intent = PaymentIntent(
            id=uuid4(),
            tenant_id=UUID(business_id),
            sale_id=UUID(sale_id),
            method="card",
            amount=float(amount),
            currency="NGN",
            status="pending",
            gateway_reference=tx_ref,
            authorization_url=card_result["link"],
        )
        await create_intent(session, intent)
        await session.commit()

    return {
        "sale_id": sale_id,
        "payment_url": card_result["link"],
        "qr_code_base64": qr_base64,
        "tx_ref": tx_ref,
        "amount": float(amount),
        "status": "pending",
    }


async def initiate_transfer_payment(
    *,
    business_id: str,
    sale_id: str,
    amount: Decimal,
    customer_email: str,
    customer_name: str | None = None,
) -> dict:
    """Initiate a bank transfer payment for a sale via Flutterwave.

    Creates a Flutterwave bank transfer charge, generates a unique bank
    account for the customer to transfer to, and persists a payment intent
    record. The transfer account is temporary and expires after a set period.

    Args:
        business_id: Unique identifier of the tenant processing the payment.
        sale_id: Unique identifier of the sale to collect payment for.
        amount: Amount to charge via bank transfer.
        customer_email: Email address of the customer for the transfer
            notification.
        customer_name: Optional name of the customer.

    Returns:
        A dictionary containing ``sale_id``, ``account_number``,
        ``bank_name``, ``amount``, ``tx_ref``, ``transfer_reference``,
        ``account_expiration``, ``transfer_note``, ``instructions``, and
        ``status: "pending"``.

    Raises:
        ValueError: If the sale is not found.
    """
    from app.sales.repository import get_sale_by_id
    from app.payments.models import PaymentIntent
    from app.payments.repository import create_intent

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sale = await get_sale_by_id(session, UUID(sale_id))
        if not sale:
            raise ValueError("sale_not_found")

        tx_ref = f"SF-TRF-{uuid4().hex[:12].upper()}"

        subaccounts = await _build_subaccounts_for_payment(
            session, UUID(business_id), float(sale.total)
        )

        transfer_result = await flutterwave_service.initiate_bank_transfer_charge(
            amount=amount,
            email=customer_email,
            tx_ref=tx_ref,
            fullname=customer_name,
            narration=f"Payment for sale {sale.sale_number}",
            meta={"business_id": business_id, "sale_id": sale_id},
            subaccounts=subaccounts,
        )

        intent = PaymentIntent(
            id=uuid4(),
            tenant_id=UUID(business_id),
            sale_id=UUID(sale_id),
            method="transfer",
            amount=float(amount),
            currency="NGN",
            status="pending",
            gateway_reference=tx_ref,
            intent_metadata=str(transfer_result),
        )
        await create_intent(session, intent)
        await session.commit()

    return {
        "sale_id": sale_id,
        "account_number": transfer_result["account_number"],
        "bank_name": transfer_result["bank_name"],
        "amount": float(amount),
        "tx_ref": tx_ref,
        "transfer_reference": transfer_result["transfer_reference"],
        "account_expiration": transfer_result["account_expiration"],
        "transfer_note": transfer_result["transfer_note"],
        "instructions": f"Transfer exactly NGN {amount:,.2f} to {transfer_result['bank_name']} account {transfer_result['account_number']}.",
        "status": "pending",
    }


async def process_split_payment(
    *,
    business_id: str,
    sale_id: str,
    splits: dict,
    customer_email: str,
    customer_name: str | None = None,
) -> dict:
    """Process a split payment across multiple methods for a single sale.

    Handles payments split across cash, card, and bank transfer methods.
    Cash payments are recorded immediately, while card and transfer payments
    initiate Flutterwave payment links. The split amounts must sum to the
    sale total. When the sale is fully paid, platform fees are calculated
    and fee ledger entries are created for cash portions.

    Args:
        business_id: Unique identifier of the tenant processing the payment.
        sale_id: Unique identifier of the sale to process split payments for.
        splits: Dictionary with ``cash``, ``card``, and ``transfer`` keys,
            each containing the amount for that payment method.
        customer_email: Email address of the customer for payment links.
        customer_name: Optional name of the customer for payment links.

    Returns:
        A dictionary containing ``sale_id``, ``total``, ``cash`` (payment
        details or ``None``), ``card`` (payment URL and details or ``None``),
        ``transfer`` (payment URL and details or ``None``), ``sale_status``,
        ``amount_paid``, and ``balance``.

    Raises:
        ValueError: If the sale is not found or the split amounts do not
            sum to the sale total.
    """
    from app.sales.repository import get_sale_by_id
    from app.payments.models import PaymentIntent
    from app.payments.repository import create_intent
    from app.catalog.qr import generate_qr_base64

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        sale = await get_sale_by_id(session, UUID(sale_id))
        if not sale:
            raise ValueError("sale_not_found")

        total = float(sale.total)
        cash_amount = Decimal(str(splits.get("cash", 0)))
        card_amount = Decimal(str(splits.get("card", 0)))
        transfer_amount = Decimal(str(splits.get("transfer", 0)))
        split_sum = float(cash_amount + card_amount + transfer_amount)

        if abs(split_sum - total) > 0.01:
            raise ValueError(f"Splits sum {split_sum} does not equal total {total}")

        result = {
            "sale_id": sale_id,
            "total": total,
            "cash": None,
            "card": None,
            "transfer": None,
        }

        if cash_amount > 0:
            from app.payments.models import Payment
            from app.payments.repository import create_payment

            payment_id = uuid4()
            payment = Payment(
                id=payment_id,
                tenant_id=UUID(business_id),
                sale_id=UUID(sale_id),
                method="cash",
                amount=float(cash_amount),
                currency="NGN",
                status="completed",
            )

            await create_payment(session, payment)

            sale.amount_paid = float(sale.amount_paid) + float(cash_amount)
            result["cash"] = {
                "amount": float(cash_amount),
                "status": "completed",
                "payment_id": str(payment_id),
            }

        if card_amount > 0:
            tx_ref = f"SF-{uuid4().hex[:16].upper()}"
            expires = datetime.now(UTC) + timedelta(minutes=30)

            subaccounts = await _build_subaccounts_for_payment(session, UUID(business_id), total)

            card_result = await flutterwave_service.create_payment_link(
                amount=card_amount,
                tx_ref=tx_ref,
                customer_email=customer_email,
                customer_name=customer_name,
                payment_options="card",
                title="StoreFlow Card Payment",
                meta={"business_id": business_id, "sale_id": sale_id},
                subaccounts=subaccounts,
            )

            qr_base64 = generate_qr_base64(card_result["link"])

            intent = PaymentIntent(
                id=uuid4(),
                tenant_id=UUID(business_id),
                sale_id=UUID(sale_id),
                method="card",
                amount=float(card_amount),
                currency="NGN",
                status="pending",
                gateway_reference=tx_ref,
                authorization_url=card_result["link"],
                expires_at=expires,
            )
            await create_intent(session, intent)

            result["card"] = {
                "amount": float(card_amount),
                "payment_url": card_result["link"],
                "qr_code_base64": qr_base64,
                "tx_ref": tx_ref,
                "expires_at": expires.isoformat(),
                "status": "pending",
            }

        if transfer_amount > 0:
            tx_ref = f"SF-TRF-{uuid4().hex[:12].upper()}"

            subaccounts = await _build_subaccounts_for_payment(session, UUID(business_id), total)

            transfer_result = await flutterwave_service.create_payment_link(
                amount=transfer_amount,
                tx_ref=tx_ref,
                customer_email=customer_email,
                customer_name=customer_name,
                payment_options="banktransfer",
                title="StoreFlow Bank Transfer",
                meta={"business_id": business_id, "sale_id": sale_id},
                subaccounts=subaccounts,
            )

            intent = PaymentIntent(
                id=uuid4(),
                tenant_id=UUID(business_id),
                sale_id=UUID(sale_id),
                method="transfer",
                amount=float(transfer_amount),
                currency="NGN",
                status="pending",
                gateway_reference=tx_ref,
                authorization_url=transfer_result["link"],
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await create_intent(session, intent)

            result["transfer"] = {
                "amount": float(transfer_amount),
                "payment_url": transfer_result["link"],
                "tx_ref": tx_ref,
                "instructions": f"Transfer exactly NGN {transfer_amount:,.2f} via the payment link.",
                "status": "pending",
            }

        pm = dict(sale.payment_methods) if sale.payment_methods else {}
        pm["split"] = {
            "cash": float(cash_amount),
            "card": float(card_amount),
            "transfer": float(transfer_amount),
        }
        pm["total"] = total
        pm["compare_total"] = float(sale.amount_paid)
        sale.payment_methods = pm

        if sale.amount_paid >= total:
            sale.status = "completed"
        elif sale.amount_paid > 0:
            sale.status = "partial"

        if sale.status == "completed":
            from app.platform.fee_calculator import calculate_platform_fee

            fee_result = await calculate_platform_fee(session, total)
            pm["platform_fee"] = fee_result["platform_fee"]
            pm["settlement_amount"] = fee_result["settlement_amount"]
            pm["fee_rule_id"] = fee_result["rule_id"]
            sale.payment_methods = pm

            if cash_amount > 0 and fee_result["platform_fee"] > 0:
                from app.platform.models import PlatformFeeLedger

                total_fee = fee_result["platform_fee"]
                cash_fee_share = (
                    total_fee * (float(cash_amount) / total) if total > 0 else total_fee
                )
                ledger = PlatformFeeLedger(
                    tenant_id=UUID(business_id),
                    sale_id=UUID(sale_id),
                    amount=round(cash_fee_share, 2),
                    fee_type=fee_result["fee_type"],
                    rate=fee_result["rate"],
                    payment_method="cash",
                    status="pending",
                )
                session.add(ledger)

        await session.commit()

        result["sale_status"] = sale.status
        result["amount_paid"] = float(sale.amount_paid)
        result["balance"] = total - float(sale.amount_paid)
        return result


async def confirm_split_card_payment(
    *,
    business_id: str,
    sale_id: str,
    tx_ref: str,
    gateway_data: dict,
) -> dict:
    """Confirm a card payment that was part of a split payment.

    Looks up the payment intent by its transaction reference, marks it as
    completed, records the payment against the sale, and updates any
    pending platform fee ledger entries to "deducted" status.

    Args:
        business_id: Unique identifier of the tenant.
        sale_id: Unique identifier of the sale the payment is against.
        tx_ref: Transaction reference for the card payment intent.
        gateway_data: Response data from the payment gateway.

    Returns:
        A dictionary containing the confirmed payment details from
        ``record_payment``.

    Raises:
        ValueError: If the payment intent is not found.
    """
    from app.payments.repository import (
        get_intent_by_reference,
        update_intent_status,
    )
    from app.platform.models import PlatformFeeLedger

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        intent = await get_intent_by_reference(session, tx_ref)
        if not intent:
            raise ValueError("intent_not_found")

        amount = Decimal(str(gateway_data.get("amount", intent.amount)))
        await update_intent_status(session, intent.id, "completed", gateway_data)

        result = await record_payment(
            business_id=business_id,
            sale_id=sale_id,
            method="card",
            amount=amount,
            reference=tx_ref,
            gateway_resp=gateway_data,
        )

        ledger_result = await session.execute(
            select(PlatformFeeLedger).where(
                PlatformFeeLedger.sale_id == UUID(sale_id),
                PlatformFeeLedger.status == "pending",
            )
        )
        for ledger in ledger_result.scalars().all():
            ledger.status = "deducted"
            ledger.settled_at = datetime.now(UTC)

        await session.commit()

    return result


async def confirm_split_transfer_payment(
    *,
    business_id: str,
    sale_id: str,
    tx_ref: str,
    gateway_data: dict,
) -> dict:
    """Confirm a bank transfer payment that was part of a split payment.

    Looks up the payment intent by its transaction reference, marks it as
    completed, records the payment against the sale, and updates any
    pending platform fee ledger entries to "deducted" status.

    Args:
        business_id: Unique identifier of the tenant.
        sale_id: Unique identifier of the sale the payment is against.
        tx_ref: Transaction reference for the transfer payment intent.
        gateway_data: Response data from the payment gateway.

    Returns:
        A dictionary containing the confirmed payment details from
        ``record_payment``.

    Raises:
        ValueError: If the payment intent is not found.
    """
    from app.payments.repository import (
        get_intent_by_reference,
        update_intent_status,
    )
    from app.platform.models import PlatformFeeLedger

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        intent = await get_intent_by_reference(session, tx_ref)
        if not intent:
            raise ValueError("intent_not_found")

        amount = Decimal(str(gateway_data.get("amount", intent.amount)))
        await update_intent_status(session, intent.id, "completed", gateway_data)

        result = await record_payment(
            business_id=business_id,
            sale_id=sale_id,
            method="transfer",
            amount=amount,
            reference=tx_ref,
            gateway_resp=gateway_data,
        )

        ledger_result = await session.execute(
            select(PlatformFeeLedger).where(
                PlatformFeeLedger.sale_id == UUID(sale_id),
                PlatformFeeLedger.status == "pending",
            )
        )
        for ledger in ledger_result.scalars().all():
            ledger.status = "deducted"
            ledger.settled_at = datetime.now(UTC)

        await session.commit()

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCUMENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def convert_document_to_sale(
    tenant_id: str,
    document_id: str,
    cashier_id: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Convert a quote or invoice document into a completed sale.

    Fetches the document and its line items, creates a sale via the sales
    service with the document's items and discount, then marks the document
    as "accepted" and links it to the newly created sale.

    Args:
        tenant_id: Unique identifier of the tenant that owns the document.
        document_id: Unique identifier of the document to convert.
        cashier_id: Identifier of the cashier or user processing the
            conversion.
        actor_id: Optional identifier of the user performing this action.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing ``document_id``, ``doc_number``,
        ``sale_id``, ``sale_number``, and ``total``.

    Raises:
        ValueError: If the document is not found, is not a quote or
            invoice, or is not in "sent" or "accepted" status.
    """
    from app.documents.repository import (
        get_document_by_id,
        get_document_items,
        update_document_status,
    )

    sdb = _get_sdb("documents")
    async with sdb.session() as session:
        doc = await get_document_by_id(session, UUID(document_id))
        if not doc or str(doc.tenant_id) != tenant_id:
            raise ValueError("document_not_found")
        if doc.doc_type not in ("quote", "invoice"):
            raise ValueError("Only quotes and invoices can be converted to sales")
        if doc.status not in ("sent", "accepted"):
            raise ValueError(f"Document must be 'sent' or 'accepted', currently '{doc.status}'")

        items = await get_document_items(session, UUID(document_id))
        sale_items = [
            {
                "product_id": str(i.product_id) if i.product_id else str(uuid4()),
                "product_name": i.description,
                "qty": i.qty,
                "unit_price": i.unit_price,
                "discount_pct": i.discount_pct,
                "tax_rate": i.tax_rate,
            }
            for i in items
        ]

        sale_result = await create_sale_via_service(
            tenant_id=tenant_id,
            cashier_id=cashier_id,
            items=sale_items,
            discount=Decimal(str(doc.discount)),
            customer_name=doc.customer_name,
            customer_phone=doc.customer_phone,
            notes=f"Converted from {doc.doc_type} {doc.doc_number}",
            correlation_id=correlation_id,
        )

        await update_document_status(session, doc.id, "accepted")
        doc.linked_sale_id = UUID(sale_result["id"])
        await session.commit()

        return {
            "document_id": document_id,
            "doc_number": doc.doc_number,
            "sale_id": sale_result["id"],
            "sale_number": sale_result["sale_number"],
            "total": sale_result["total"],
        }


async def create_document(
    tenant_id: str,
    actor_id: str,
    doc_type: str,
    items: list[dict],
    customer_name: str | None = None,
    customer_email: str | None = None,
    customer_phone: str | None = None,
    customer_address: str | None = None,
    due_date=None,
    notes: str | None = None,
    terms: str | None = None,
    linked_sale_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a business document (quote, invoice, receipt, or delivery note).

    Plans and persists a new document with its line items through the
    documents service. Documents track customer-facing financial records
    and can optionally be linked to an existing sale.

    Args:
        tenant_id: Unique identifier of the tenant that owns the document.
        actor_id: Identifier of the user creating the document.
        doc_type: Type of document to create (e.g. "quote", "invoice",
            "receipt", "delivery_note").
        items: List of document item dictionaries, each containing
            ``description``, ``qty``, ``unit_price``, and optionally
            ``product_id``, ``discount_pct``, and ``tax_rate``.
        customer_name: Optional customer name for the document.
        customer_email: Optional customer email for the document.
        customer_phone: Optional customer phone for the document.
        customer_address: Optional customer address for the document.
        due_date: Optional payment due date for invoices.
        notes: Optional notes for the document.
        terms: Optional terms and conditions text.
        linked_sale_id: Optional sale identifier to link this document to.
        correlation_id: Optional correlation identifier for distributed
            tracing.

    Returns:
        A dictionary containing the newly created document details
        including ``id``, ``doc_number``, ``doc_type``, ``status``,
        ``total``, and ``created_at``.
    """
    from app.documents.schemas import DocumentCreateCommand, DocumentItemLine
    from app.documents.service import plan_document_creation
    from app.documents.repository import (
        create_document as repo_create_document,
        create_document_items,
    )

    item_lines = [
        DocumentItemLine(
            product_id=UUID(i["product_id"]) if i.get("product_id") else None,
            description=i["description"],
            qty=Decimal(str(i["qty"])),
            unit_price=Decimal(str(i["unit_price"])),
            discount_pct=Decimal(str(i.get("discount_pct", 0))),
            tax_rate=Decimal(str(i["tax_rate"])) if i.get("tax_rate") else None,
        )
        for i in items
    ]

    command = DocumentCreateCommand(
        tenant_id=UUID(tenant_id),
        actor_id=UUID(actor_id),
        doc_type=doc_type,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        customer_address=customer_address,
        due_date=due_date,
        notes=notes,
        terms=terms,
        items=item_lines,
        linked_sale_id=UUID(linked_sale_id) if linked_sale_id else None,
        correlation_id=correlation_id,
    )

    result, doc_model, item_models, outbox = plan_document_creation(command)
    sdb = _get_sdb("documents")
    async with sdb.session() as session:
        await repo_create_document(session, doc_model)
        await create_document_items(session, item_models)
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

    return result.model_dump(mode="json")


async def list_documents(
    tenant_id: str,
    doc_type: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List documents belonging to a tenant with optional filtering.

    Retrieves a paginated list of documents from the documents database,
    optionally filtered by document type and status.

    Args:
        tenant_id: Unique identifier of the tenant whose documents are
            being listed.
        doc_type: Optional document type filter (e.g. "quote", "invoice").
        status: Optional status filter (e.g. "draft", "sent", "accepted").
        page: Page number for pagination (1-indexed).
        page_size: Number of items per page.

    Returns:
        A dictionary containing ``items`` (list of document dictionaries),
        ``total`` count, ``page``, and ``page_size``.
    """
    from app.documents.repository import list_documents_by_tenant

    sdb = _get_sdb("documents")
    async with sdb.session() as session:
        items = await list_documents_by_tenant(
            session,
            tenant_id=UUID(tenant_id),
            doc_type=doc_type,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "items": [
                {
                    "id": str(d.id),
                    "doc_number": d.doc_number,
                    "doc_type": d.doc_type,
                    "status": d.status,
                    "customer_name": d.customer_name,
                    "total": float(d.total),
                    "due_date": d.due_date.isoformat() if d.due_date else None,
                    "linked_sale_id": str(d.linked_sale_id) if d.linked_sale_id else None,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in items
            ],
            "total": len(items),
            "page": page,
            "page_size": page_size,
        }


async def get_document_by_id(tenant_id: str, document_id: str) -> dict | None:
    """Retrieve a specific document and its line items by identifier.

    Fetches the document from the documents database along with all
    associated line items, and validates that it belongs to the specified
    tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the document.
        document_id: Unique identifier of the document to retrieve.

    Returns:
        A dictionary containing the full document details including ``id``,
        ``doc_number``, ``doc_type``, ``status``, ``customer_name``,
        ``subtotal``, ``discount``, ``tax``, ``total``, ``due_date``,
        ``notes``, ``terms``, ``linked_sale_id``, ``pdf_url``,
        ``created_at``, and ``items`` (list of item dictionaries), or
        ``None`` if the document does not exist.
    """
    from app.documents.repository import get_document_by_id, get_document_items

    sdb = _get_sdb("documents")
    async with sdb.session() as session:
        doc = await get_document_by_id(session, UUID(document_id))
        if not doc or str(doc.tenant_id) != tenant_id:
            return None
        items = await get_document_items(session, UUID(document_id))
        return {
            "id": str(doc.id),
            "doc_number": doc.doc_number,
            "doc_type": doc.doc_type,
            "status": doc.status,
            "customer_name": doc.customer_name,
            "customer_email": doc.customer_email,
            "customer_phone": doc.customer_phone,
            "customer_address": doc.customer_address,
            "subtotal": float(doc.subtotal),
            "discount": float(doc.discount),
            "tax": str(doc.tax),
            "total": str(doc.total),
            "due_date": doc.due_date.isoformat() if doc.due_date else None,
            "notes": doc.notes,
            "terms": doc.terms,
            "linked_sale_id": str(doc.linked_sale_id) if doc.linked_sale_id else None,
            "pdf_url": doc.pdf_url,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "items": [
                {
                    "id": str(i.id),
                    "product_id": str(i.product_id) if i.product_id else None,
                    "description": i.description,
                    "qty": int(i.qty),
                    "unit_price": str(i.unit_price),
                    "discount_pct": str(i.discount_pct),
                    "tax_rate": str(i.tax_rate) if i.tax_rate else None,
                    "line_total": str(i.line_total),
                }
                for i in items
            ],
        }


async def update_document_status(
    tenant_id: str,
    document_id: str,
    actor_id: str,
    new_status: str,
) -> dict:
    """Update the status of a document.

    Changes the document's status (e.g. from "draft" to "sent") after
    validating the transition through the documents service. Records
    outbox events for downstream notification.

    Args:
        tenant_id: Unique identifier of the tenant that owns the document.
        document_id: Unique identifier of the document to update.
        actor_id: Identifier of the user performing the status change.
        new_status: The new status to set on the document.

    Returns:
        A dictionary containing ``id``, ``doc_number``, and ``status``
        confirming the update.

    Raises:
        ValueError: If the document is not found or the status transition
            is invalid.
    """
    from app.documents.repository import (
        get_document_by_id,
        update_document_status as repo_update_status,
    )
    from app.documents.schemas import DocumentStatusCommand
    from app.documents.service import plan_status_change

    sdb = _get_sdb("documents")
    async with sdb.session() as session:
        doc = await get_document_by_id(session, UUID(document_id))
        if not doc or str(doc.tenant_id) != tenant_id:
            raise ValueError("document_not_found")

        command = DocumentStatusCommand(
            document_id=UUID(document_id),
            tenant_id=UUID(tenant_id),
            actor_id=UUID(actor_id),
            new_status=new_status,
        )

        updated_doc, outbox = plan_status_change(command, doc)
        await repo_update_status(session, doc.id, new_status)
        for write in outbox:
            session.add(write.to_model())
        await session.commit()

        return {
            "id": str(doc.id),
            "doc_number": doc.doc_number,
            "status": new_status,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  CUSTOM ROLE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


async def create_role_for_tenant(
    tenant_id: str,
    name: str,
    rank: int,
    description: str | None = None,
    permission_ids: list[str] | None = None,
) -> dict:
    """Create a new custom role for a tenant.

    Creates a role in the identity database with the specified name, rank,
    and optional permission assignments. Role names must be unique within
    a tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the role.
        name: Display name of the role (e.g. "store_manager").
        rank: Numerical rank determining the role's authority level
            (higher values indicate greater authority).
        description: Optional description of the role's purpose.
        permission_ids: Optional list of permission identifiers to assign
            to the role.

    Returns:
        A dictionary containing ``id``, ``name``, ``rank``, ``description``,
        and ``tenant_id``.

    Raises:
        ValueError: If a role with the same name already exists for the
            tenant.
    """
    from app.identity.repository import (
        create_role as repo_create_role,
        get_role_by_name_for_tenant,
        set_role_permissions,
    )

    tenant_uid = UUID(tenant_id)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        existing = await get_role_by_name_for_tenant(session, tenant_uid, name)
        if existing:
            raise ValueError(f"Role '{name}' already exists for this tenant")

        role = await repo_create_role(
            session, tenant_id=tenant_uid, name=name, rank=rank, description=description
        )

        if permission_ids:
            await set_role_permissions(session, role.id, [UUID(pid) for pid in permission_ids])

        await session.commit()

        return {
            "id": str(role.id),
            "name": role.name,
            "rank": role.rank,
            "description": role.description,
            "tenant_id": str(role.tenant_id),
        }


async def update_role_for_tenant(
    tenant_id: str,
    role_id: str,
    name: str | None = None,
    rank: int | None = None,
    description: str | None = None,
) -> dict | None:
    """Update an existing role's details for a tenant.

    Modifies the name, rank, or description of a role. The role must
    belong to the specified tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the role.
        role_id: Unique identifier of the role to update.
        name: Optional new display name for the role.
        rank: Optional new rank value.
        description: Optional new description for the role.

    Returns:
        A dictionary containing the updated role details including ``id``,
        ``name``, ``rank``, ``description``, and ``tenant_id``, or ``None``
        if the role does not exist.
    """
    from app.identity.repository import get_role_by_id, update_role as repo_update_role

    role_uid = UUID(role_id)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        role = await get_role_by_id(session, role_uid)
        if not role or str(role.tenant_id) != tenant_id:
            return None

        updated = await repo_update_role(
            session, role_uid, name=name, rank=rank, description=description
        )
        await session.commit()

        return {
            "id": str(updated.id),
            "name": updated.name,
            "rank": updated.rank,
            "description": updated.description,
            "tenant_id": str(updated.tenant_id),
        }


async def delete_role_for_tenant(tenant_id: str, role_id: str) -> bool:
    """Delete a custom role from a tenant.

    Removes the role record from the identity database. The role must
    belong to the specified tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the role.
        role_id: Unique identifier of the role to delete.

    Returns:
        ``True`` if the role was successfully deleted, ``False`` if the
        role was not found.
    """
    from app.identity.repository import get_role_by_id, delete_role as repo_delete_role

    role_uid = UUID(role_id)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        role = await get_role_by_id(session, role_uid)
        if not role or str(role.tenant_id) != tenant_id:
            return False

        result = await repo_delete_role(session, role_uid)
        await session.commit()
        return result


async def set_role_permissions_for_tenant(
    tenant_id: str,
    role_id: str,
    permission_ids: list[str],
) -> dict | None:
    """Replace all permissions for a role with a new set.

    Clears the existing permission assignments for the role and sets them
    to the provided list of permission identifiers.

    Args:
        tenant_id: Unique identifier of the tenant that owns the role.
        role_id: Unique identifier of the role to update permissions for.
        permission_ids: List of permission identifiers to assign to the
            role.

    Returns:
        A dictionary containing ``id``, ``name``, and ``permissions``
        (list of permission name strings), or ``None`` if the role does
        not exist.
    """
    from app.identity.repository import (
        get_role_by_id,
        set_role_permissions as repo_set_permissions,
    )

    role_uid = UUID(role_id)
    sdb = _get_sdb("identity")
    async with sdb.session() as session:
        role = await get_role_by_id(session, role_uid)
        if not role or str(role.tenant_id) != tenant_id:
            return None

        await repo_set_permissions(session, role_uid, [UUID(pid) for pid in permission_ids])
        await session.commit()

        from app.identity.repository import get_permissions_for_role

        perms = await get_permissions_for_role(session, role_uid)
        return {
            "id": str(role.id),
            "name": role.name,
            "permissions": [p.name for p in perms],
        }


# ── Customers ────────────────────────────────────────────────────────


def _customer_to_dict(c) -> dict:
    """Convert a Customer model instance to a dictionary.

    Args:
        c: A ``Customer`` model instance from the customers database.

    Returns:
        A dictionary containing ``id``, ``name``, ``phone``, ``email``,
        ``address``, ``created_at``, and ``updated_at``.
    """
    return {
        "id": str(c.id),
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "address": c.address,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@cached(
    prefix="customers:list",
    ttl=300,
    key_func=lambda tenant_id, page=1, page_size=50, search=None, **kw: f"{page}:{page_size}:{search or ''}",
)
async def list_customers(
    tenant_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
) -> dict:
    """List customers belonging to a tenant with optional search.

    Retrieves a paginated list of customers from the customers database,
    with optional text search across name, email, and phone fields.

    Args:
        tenant_id: Unique identifier of the tenant whose customers are
            being listed.
        page: Page number for pagination (1-indexed).
        page_size: Number of items per page.
        search: Optional search term to filter customers by name, email,
            or phone.

    Returns:
        A dictionary containing ``items`` (list of customer dictionaries),
        ``total`` count, ``page``, and ``page_size``.
    """
    from app.customers.models import Customer
    from sqlalchemy import func, select

    tenant_uid = UUID(tenant_id)
    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        q = select(Customer).where(Customer.tenant_id == tenant_uid)
        cq = select(func.count(Customer.id)).where(Customer.tenant_id == tenant_uid)

        if search:
            like = f"%{search}%"
            q = q.where(
                Customer.name.ilike(like) | Customer.email.ilike(like) | Customer.phone.ilike(like)
            )
            cq = cq.where(
                Customer.name.ilike(like) | Customer.email.ilike(like) | Customer.phone.ilike(like)
            )

        total = (await session.execute(cq)).scalar() or 0
        rows = (
            (
                await session.execute(
                    q.order_by(Customer.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

        return {
            "items": [_customer_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def get_customer(tenant_id: str, customer_id: str) -> dict | None:
    """Retrieve a specific customer by their identifier.

    Fetches the customer from the customers database and validates that
    they belong to the specified tenant.

    Args:
        tenant_id: Unique identifier of the tenant that owns the customer.
        customer_id: Unique identifier of the customer to retrieve.

    Returns:
        A dictionary containing the customer details including ``id``,
        ``name``, ``phone``, ``email``, ``address``, ``created_at``,
        and ``updated_at``, or ``None`` if the customer does not exist.
    """
    from app.customers.models import Customer
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        row = (
            await session.execute(
                select(Customer).where(
                    Customer.id == UUID(customer_id), Customer.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not row:
            return None
        return _customer_to_dict(row)


async def create_customer(
    tenant_id: str,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
) -> dict:
    """Create a new customer record for a tenant.

    Persists a new customer in the customers database with the provided
    contact information.

    Args:
        tenant_id: Unique identifier of the tenant that owns the customer.
        name: Full name of the customer.
        phone: Optional phone number of the customer.
        email: Optional email address of the customer.
        address: Optional physical address of the customer.

    Returns:
        A dictionary containing the newly created customer details
        including ``id``, ``name``, ``phone``, ``email``, ``address``,
        ``created_at``, and ``updated_at``.
    """
    from app.customers.models import Customer

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        customer = Customer(
            tenant_id=UUID(tenant_id),
            name=name,
            phone=phone,
            email=email,
            address=address,
        )
        session.add(customer)
        await session.flush()
        result = _customer_to_dict(customer)
        await session.commit()
        await cache.delete_pattern(f"sf:cache:customers:list:{tenant_id}*")
        return result


async def update_customer(
    tenant_id: str,
    customer_id: str,
    **kwargs,
) -> dict | None:
    """Update an existing customer's details.

    Modifies the specified fields on a customer record. Only ``name``,
    ``phone``, ``email``, and ``address`` fields are updated.

    Args:
        tenant_id: Unique identifier of the tenant that owns the customer.
        customer_id: Unique identifier of the customer to update.
        **kwargs: Arbitrary keyword arguments representing the fields to
            update (e.g. ``name``, ``phone``, ``email``, ``address``).

    Returns:
        A dictionary containing the updated customer details, or ``None``
        if the customer does not exist.
    """
    from app.customers.models import Customer
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        row = (
            await session.execute(
                select(Customer).where(
                    Customer.id == UUID(customer_id), Customer.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not row:
            return None

        for key in ("name", "phone", "email", "address"):
            val = kwargs.get(key)
            if val is not None:
                setattr(row, key, val)

        await session.flush()
        result = _customer_to_dict(row)
        await session.commit()
        await cache.delete_pattern(f"sf:cache:customers:list:{tenant_id}*")
        return result


# ── Stock Balances ──────────────────────────────────────────────────


async def list_stock_balances(
    tenant_id: str,
    store_id: str | None = None,
    product_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List stock balances across stores with optional filtering.

    Retrieves a paginated list of stock balance records joined with product
    catalog data. Supports filtering by store and product, and returns
    inventory levels including quantity, reserved quantity, and unit cost.

    Args:
        tenant_id: Unique identifier of the tenant.
        store_id: Optional store identifier to filter balances to a
            specific store.
        product_id: Optional product identifier to filter balances to a
            specific product.
        page: Page number for pagination (1-indexed).
        page_size: Number of items per page.

    Returns:
        A dictionary containing ``items`` (list of stock balance
        dictionaries with product details), ``total`` count, ``page``,
        and ``page_size``.
    """
    from app.inventory.models import StockBalance
    from app.catalog.models import Product
    from sqlalchemy import func, select

    tenant_uid = UUID(tenant_id)
    sdb = _get_sdb("inventory")
    async with sdb.session() as session:
        q = (
            select(
                StockBalance.id.label("sb_id"),
                StockBalance.product_id,
                StockBalance.store_id,
                StockBalance.qty,
                StockBalance.reserved_qty,
                StockBalance.min_stock_level,
                StockBalance.unit_cost,
                Product.name.label("product_name"),
                Product.sku.label("product_sku"),
            )
            .join(Product, Product.id == StockBalance.product_id)
            .where(StockBalance.tenant_id == tenant_uid)
        )
        cq = select(func.count(StockBalance.id)).where(StockBalance.tenant_id == tenant_uid)

        if store_id:
            q = q.where(StockBalance.store_id == UUID(store_id))
            cq = cq.where(StockBalance.store_id == UUID(store_id))
        if product_id:
            q = q.where(StockBalance.product_id == UUID(product_id))
            cq = cq.where(StockBalance.product_id == UUID(product_id))

        total = (await session.execute(cq)).scalar() or 0
        rows = (
            (
                await session.execute(
                    q.order_by(Product.name).offset((page - 1) * page_size).limit(page_size)
                )
            )
            .mappings()
            .all()
        )

        return {
            "items": [
                {
                    "id": str(r["sb_id"]),
                    "product_id": str(r["product_id"]),
                    "store_id": str(r["store_id"]) if r["store_id"] else None,
                    "product_name": r["product_name"],
                    "product_sku": r["product_sku"],
                    "quantity": r["qty"],
                    "reserved_qty": r["reserved_qty"],
                    "min_stock_level": r["min_stock_level"],
                    "reorder_point": r["min_stock_level"],
                    "unit_cost": r["unit_cost"] if r["unit_cost"] else None,
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def delete_customer(tenant_id: str, customer_id: str) -> bool:
    """Permanently delete a customer record.

    Removes the customer from the customers database. This is a hard delete
    and cannot be undone.

    Args:
        tenant_id: Unique identifier of the tenant that owns the customer.
        customer_id: Unique identifier of the customer to delete.

    Returns:
        ``True`` if the customer was successfully deleted, ``False`` if the
        customer was not found.
    """
    from app.customers.models import Customer
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        row = (
            await session.execute(
                select(Customer).where(
                    Customer.id == UUID(customer_id), Customer.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


# ═══════════════════════════════════════════════════════════════════════════════
#  DISCOUNTS & COUPONS
# ═══════════════════════════════════════════════════════════════════════════════


def _discount_to_dict(d, product_ids=None, category_ids=None) -> dict:
    return {
        "id": str(d.id),
        "name": d.name,
        "discount_type": d.discount_type,
        "value": float(d.value),
        "buy_x_get_y_free_qty": d.buy_x_get_y_free_qty,
        "scope": d.scope,
        "min_order": float(d.min_order),
        "is_active": d.is_active,
        "start_date": d.start_date.isoformat() if d.start_date else None,
        "end_date": d.end_date.isoformat() if d.end_date else None,
        "product_ids": [str(p) for p in (product_ids or [])],
        "category_ids": [str(c) for c in (category_ids or [])],
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _coupon_to_dict(c) -> dict:
    return {
        "id": str(c.id),
        "code": c.code,
        "discount_type": c.discount_type,
        "value": float(c.value),
        "max_uses": c.max_uses,
        "used_count": c.used_count,
        "min_order": float(c.min_order),
        "is_active": c.is_active,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


async def create_discount(
    tenant_id: str,
    name: str,
    discount_type: str,
    value,
    buy_x_get_y_free_qty: int = 0,
    scope: str = "all",
    min_order=0,
    is_active: bool = True,
    start_date=None,
    end_date=None,
    product_ids: list[str] | None = None,
    category_ids: list[str] | None = None,
) -> dict:
    from app.discounts.models import Discount, DiscountProduct, DiscountCategory

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        discount = Discount(
            tenant_id=UUID(tenant_id),
            name=name,
            discount_type=discount_type,
            value=Decimal(str(value)),
            buy_x_get_y_free_qty=buy_x_get_y_free_qty,
            scope=scope,
            min_order=Decimal(str(min_order)),
            is_active=is_active,
            start_date=start_date,
            end_date=end_date,
        )
        session.add(discount)
        await session.flush()

        linked_product_ids = []
        if scope == "specific_products" and product_ids:
            for pid in product_ids:
                dp = DiscountProduct(discount_id=discount.id, product_id=UUID(pid))
                session.add(dp)
                linked_product_ids.append(pid)

        linked_category_ids = []
        if scope == "specific_categories" and category_ids:
            for cid in category_ids:
                dc = DiscountCategory(discount_id=discount.id, category_id=UUID(cid))
                session.add(dc)
                linked_category_ids.append(cid)

        result = _discount_to_dict(discount, linked_product_ids, linked_category_ids)
        await session.commit()
        return result


async def list_discounts(
    tenant_id: str,
    page: int = 1,
    page_size: int = 50,
    active_only: bool = False,
) -> dict:
    from app.discounts.models import Discount, DiscountProduct, DiscountCategory
    from sqlalchemy import func, select

    tid = UUID(tenant_id)
    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        q = select(Discount).where(Discount.tenant_id == tid)
        cq = select(func.count(Discount.id)).where(Discount.tenant_id == tid)

        if active_only:
            q = q.where(Discount.is_active == True)
            cq = cq.where(Discount.is_active == True)

        total = (await session.execute(cq)).scalar() or 0
        rows = (
            await session.execute(
                q.order_by(Discount.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()

        items = []
        for d in rows:
            pids = (
                await session.execute(
                    select(DiscountProduct.product_id).where(DiscountProduct.discount_id == d.id)
                )
            ).scalars().all()
            cids = (
                await session.execute(
                    select(DiscountCategory.category_id).where(DiscountCategory.discount_id == d.id)
                )
            ).scalars().all()
            items.append(_discount_to_dict(d, pids, cids))

        return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_discount(tenant_id: str, discount_id: str) -> dict | None:
    from app.discounts.models import Discount, DiscountProduct, DiscountCategory
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        d = (
            await session.execute(
                select(Discount).where(
                    Discount.id == UUID(discount_id), Discount.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not d:
            return None
        pids = (
            await session.execute(
                select(DiscountProduct.product_id).where(DiscountProduct.discount_id == d.id)
            )
        ).scalars().all()
        cids = (
            await session.execute(
                select(DiscountCategory.category_id).where(DiscountCategory.discount_id == d.id)
            )
        ).scalars().all()
        return _discount_to_dict(d, pids, cids)


async def update_discount(tenant_id: str, discount_id: str, **kwargs) -> dict | None:
    from app.discounts.models import Discount, DiscountProduct, DiscountCategory
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        d = (
            await session.execute(
                select(Discount).where(
                    Discount.id == UUID(discount_id), Discount.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not d:
            return None

        product_ids = kwargs.pop("product_ids", None)
        category_ids = kwargs.pop("category_ids", None)

        allowed = {"name", "discount_type", "value", "buy_x_get_y_free_qty", "scope", "min_order", "is_active", "start_date", "end_date"}
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                if k in ("value", "min_order"):
                    v = Decimal(str(v))
                setattr(d, k, v)

        if product_ids is not None:
            await session.execute(
                DiscountProduct.__table__.delete().where(DiscountProduct.discount_id == d.id)
            )
            for pid in product_ids:
                session.add(DiscountProduct(discount_id=d.id, product_id=UUID(pid)))

        if category_ids is not None:
            await session.execute(
                DiscountCategory.__table__.delete().where(DiscountCategory.discount_id == d.id)
            )
            for cid in category_ids:
                session.add(DiscountCategory(discount_id=d.id, category_id=UUID(cid)))

        await session.flush()
        pids = (
            await session.execute(
                select(DiscountProduct.product_id).where(DiscountProduct.discount_id == d.id)
            )
        ).scalars().all()
        cids = (
            await session.execute(
                select(DiscountCategory.category_id).where(DiscountCategory.discount_id == d.id)
            )
        ).scalars().all()
        result = _discount_to_dict(d, pids, cids)
        await session.commit()
        return result


async def toggle_discount(tenant_id: str, discount_id: str) -> dict | None:
    from app.discounts.models import Discount
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        d = (
            await session.execute(
                select(Discount).where(
                    Discount.id == UUID(discount_id), Discount.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not d:
            return None
        d.is_active = not d.is_active
        await session.flush()
        result = _discount_to_dict(d)
        await session.commit()
        return result


async def delete_discount(tenant_id: str, discount_id: str) -> bool:
    from app.discounts.models import Discount
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        d = (
            await session.execute(
                select(Discount).where(
                    Discount.id == UUID(discount_id), Discount.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not d:
            return False
        await session.delete(d)
        await session.commit()
        return True


# ── Coupons ────────────────────────────────────────────────────────────────


async def create_coupon(
    tenant_id: str,
    code: str,
    discount_type: str,
    value,
    max_uses: int = 0,
    min_order=0,
    is_active: bool = True,
    expires_at=None,
) -> dict:
    from app.discounts.models import Coupon

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        coupon = Coupon(
            tenant_id=UUID(tenant_id),
            code=code.upper().strip(),
            discount_type=discount_type,
            value=Decimal(str(value)),
            max_uses=max_uses,
            min_order=Decimal(str(min_order)),
            is_active=is_active,
            expires_at=expires_at,
        )
        session.add(coupon)
        await session.flush()
        result = _coupon_to_dict(coupon)
        await session.commit()
        return result


async def list_coupons(tenant_id: str, page: int = 1, page_size: int = 50) -> dict:
    from app.discounts.models import Coupon
    from sqlalchemy import func, select

    tid = UUID(tenant_id)
    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        q = select(Coupon).where(Coupon.tenant_id == tid)
        cq = select(func.count(Coupon.id)).where(Coupon.tenant_id == tid)

        total = (await session.execute(cq)).scalar() or 0
        rows = (
            await session.execute(
                q.order_by(Coupon.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()

        return {
            "items": [_coupon_to_dict(c) for c in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def get_coupon(tenant_id: str, coupon_id: str) -> dict | None:
    from app.discounts.models import Coupon
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        c = (
            await session.execute(
                select(Coupon).where(
                    Coupon.id == UUID(coupon_id), Coupon.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        return _coupon_to_dict(c) if c else None


async def update_coupon(tenant_id: str, coupon_id: str, **kwargs) -> dict | None:
    from app.discounts.models import Coupon
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        c = (
            await session.execute(
                select(Coupon).where(
                    Coupon.id == UUID(coupon_id), Coupon.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not c:
            return None

        allowed = {"code", "discount_type", "value", "max_uses", "min_order", "is_active", "expires_at"}
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                if k == "code":
                    v = v.upper().strip()
                if k in ("value", "min_order"):
                    v = Decimal(str(v))
                setattr(c, k, v)

        await session.flush()
        result = _coupon_to_dict(c)
        await session.commit()
        return result


async def delete_coupon(tenant_id: str, coupon_id: str) -> bool:
    from app.discounts.models import Coupon
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        c = (
            await session.execute(
                select(Coupon).where(
                    Coupon.id == UUID(coupon_id), Coupon.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if not c:
            return False
        await session.delete(c)
        await session.commit()
        return True


async def validate_coupon(tenant_id: str, code: str, cart_subtotal) -> dict:
    from app.discounts.models import Coupon
    from sqlalchemy import select
    from datetime import UTC, datetime

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        c = (
            await session.execute(
                select(Coupon).where(
                    Coupon.tenant_id == UUID(tenant_id),
                    Coupon.code == code.upper().strip(),
                )
            )
        ).scalar_one_or_none()

        if not c:
            return {"valid": False, "message": "Coupon not found"}

        if not c.is_active:
            return {"valid": False, "message": "Coupon is disabled"}

        if c.expires_at and c.expires_at < datetime.now(UTC):
            return {"valid": False, "message": "Coupon has expired"}

        if c.max_uses > 0 and c.used_count >= c.max_uses:
            return {"valid": False, "message": "Coupon usage limit reached"}

        subtotal = Decimal(str(cart_subtotal))
        if subtotal < c.min_order:
            return {
                "valid": False,
                "message": f"Minimum order of ₦{c.min_order:,.2f} required",
            }

        if c.discount_type == "percentage":
            discount_amount = float(subtotal * c.value / 100)
        else:
            discount_amount = min(float(c.value), float(subtotal))

        final_total = max(float(subtotal) - discount_amount, 0)

        return {
            "valid": True,
            "coupon_id": str(c.id),
            "code": c.code,
            "discount_type": c.discount_type,
            "discount_amount": round(discount_amount, 2),
            "final_total": round(final_total, 2),
            "message": "Coupon applied successfully",
        }


async def apply_discount(
    subtotal,
    discount_type: str,
    value,
    buy_x_get_y_free_qty: int = 0,
    cart_items: list[dict] | None = None,
) -> float:
    """Calculate discount amount for a promotion."""
    sub = Decimal(str(subtotal))

    if discount_type == "percentage":
        return round(float(sub * Decimal(str(value)) / 100), 2)

    if discount_type == "fixed_amount":
        return round(min(float(value), float(sub)), 2)

    if discount_type == "buy_x_get_y" and cart_items:
        total_free = Decimal("0")
        for item in cart_items:
            qty = Decimal(str(item.get("qty", 0)))
            unit_price = Decimal(str(item.get("unit_price", 0)))
            if buy_x_get_y_free_qty > 0:
                free_sets = qty // (qty + buy_x_get_y_free_qty)
                # Actually: for every (buy_qty + free_qty), you get free_qty free
                # But we only have free_qty, so assume buy_qty = 1
                free_sets = qty // (1 + buy_x_get_y_free_qty)
                total_free += free_sets * buy_x_get_y_free_qty * unit_price
        return round(float(total_free), 2)

    return 0.0


async def increment_coupon_usage(tenant_id: str, coupon_id: str) -> None:
    """Increment the used_count on a coupon after successful checkout."""
    from app.discounts.models import Coupon
    from sqlalchemy import select

    sdb = _get_sdb("customers")
    async with sdb.session() as session:
        c = (
            await session.execute(
                select(Coupon).where(
                    Coupon.id == UUID(coupon_id), Coupon.tenant_id == UUID(tenant_id)
                )
            )
        ).scalar_one_or_none()
        if c:
            c.used_count += 1
            await session.commit()
