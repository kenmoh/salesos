from uuid import UUID, uuid4

from catalog.events import product_created_event, product_qr_generated_event
from catalog.ids import new_product_public_id
from catalog.models import Category
from catalog.schemas import (
    CategoryCreateCommand,
    CategoryCreateResult,
    ProductCreateCommand,
    ProductCreateResult,
)
from common.events.outbox import OutboxWrite


def plan_product_creation(
    command: ProductCreateCommand,
) -> tuple[ProductCreateResult, list[OutboxWrite]]:
    """Prepare product creation side effects without touching infrastructure.

    The repository layer will persist the product and outbox rows in one DB transaction.
    Keeping this function pure makes the QR and event contract easy to test.
    """

    product_id = uuid4()
    public_id = new_product_public_id()

    created = product_created_event(
        tenant_id=command.tenant_id,
        actor_id=command.actor_id,
        product_id=product_id,
        public_id=public_id,
        name=command.name,
        correlation_id=command.correlation_id,
    )
    qr_generated = product_qr_generated_event(
        tenant_id=command.tenant_id,
        actor_id=command.actor_id,
        product_id=product_id,
        public_id=public_id,
        qr_payload=str(product_id),
        qr_asset_url=None,
        correlation_id=command.correlation_id,
    )
    result = ProductCreateResult(
        product_id=product_id,
        public_id=public_id,
        qr_payload=str(product_id),
        qr_url=None,
    )
    events = [
        OutboxWrite(event=created, aggregate_type="product", aggregate_id=str(product_id)),
        OutboxWrite(event=qr_generated, aggregate_type="product", aggregate_id=str(product_id)),
    ]
    return result, events


def plan_category_creation(
    command: CategoryCreateCommand,
    tenant_id: UUID,
) -> tuple[CategoryCreateResult, Category]:
    """Prepare category creation side effects (pure function)."""

    category_id = uuid4()
    category = Category(
        id=category_id,
        tenant_id=tenant_id,
        store_id=command.store_id,
        name=command.name,
        description=command.description,
    )
    result = CategoryCreateResult(
        category_id=category_id,
        name=command.name,
    )
    return result, category
