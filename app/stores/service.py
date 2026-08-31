from uuid import uuid4

from app.stores.models import Store
from app.stores.schemas import StoreCreateCommand, StoreResult


def plan_create_store(
    command: StoreCreateCommand,
    existing_main_count: int = 0,
) -> tuple[StoreResult, Store]:
    if command.is_warehouse and existing_main_count > 0:
        raise ValueError("main_store_exists")

    store_id = uuid4()
    store = Store(
        id=store_id,
        tenant_id=command.tenant_id,
        name=command.name,
        address=command.address,
        is_warehouse=command.is_warehouse,
        status="active",
    )
    result = StoreResult(
        id=store_id,
        tenant_id=command.tenant_id,
        name=command.name,
        address=command.address,
        is_warehouse=command.is_warehouse,
        status="active",
    )
    return result, store
