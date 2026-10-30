"""Edits made from a store's product screen have to reach the rows that screen reads.

The product grid and detail endpoint read catalog.products, while the store
PATCH wrote store_products. The request succeeded and nothing changed on
screen. Category writes had the mirror problem on the read side: their cache
bump used the store id as the tenant, so the category list stayed stale until
the five-minute TTL ran out.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

TENANT = uuid4()
STORE = uuid4()
PRODUCT = uuid4()


def _session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


class TestStoreProductEditReachesCatalog:
    async def test_a_name_and_price_edit_update_the_catalog_row(self):
        from app.common import bridge
        from app.stores import repository

        inventory_session = _session()
        catalog_session = _session()
        sdb = MagicMock()
        sdb.session.side_effect = [inventory_session, catalog_session]

        result = MagicMock()
        result.id = uuid4()
        result.store_id = STORE
        result.product_id = PRODUCT
        result.name = "Renamed Rice"
        result.sku = None
        result.selling_price = Decimal("1500")
        result.cost_price = Decimal("1000")
        result.status = "active"

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", AsyncMock()),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "update_store_product", AsyncMock(return_value=result)
            ),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                name="Renamed Rice",
                selling_price=Decimal("1500"),
                cost_price=Decimal("1000"),
            )

        stmt = catalog_session.execute.call_args[0][0]
        params = stmt.compile().params
        assert params["name"] == "Renamed Rice"
        assert params["selling_price"] == Decimal("1500")
        assert params["cost_price"] == Decimal("1000")

    async def test_a_category_edit_updates_the_catalog_row(self):
        from app.common import bridge
        from app.stores import repository

        category = uuid4()
        validation_session = _session()
        found = MagicMock()
        found.scalar_one_or_none.return_value = category
        validation_session.execute = AsyncMock(return_value=found)
        inventory_session = _session()
        catalog_session = _session()
        sdb = MagicMock()
        sdb.session.side_effect = [
            validation_session,
            inventory_session,
            catalog_session,
        ]

        result = MagicMock()
        result.id = uuid4()
        result.selling_price = Decimal("10")
        result.cost_price = Decimal("5")

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", AsyncMock()),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "update_store_product", AsyncMock(return_value=result)
            ),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                category_id=str(category),
            )

        stmt = catalog_session.execute.call_args[0][0]
        assert stmt.compile().params["category_id"] == category

    async def test_an_expiry_warning_window_reaches_the_catalog_row(self):
        from app.common import bridge
        from app.stores import repository

        inventory_session = _session()
        catalog_session = _session()
        sdb = MagicMock()
        sdb.session.side_effect = [inventory_session, catalog_session]

        result = MagicMock()
        result.id = uuid4()
        result.selling_price = Decimal("10")
        result.cost_price = Decimal("5")

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", AsyncMock()),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "update_store_product", AsyncMock(return_value=result)
            ),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                expiry_warning_days=7,
            )

        stmt = catalog_session.execute.call_args[0][0]
        assert stmt.compile().params["expiry_warning_days"] == 7

    async def test_a_sale_block_reaches_the_catalog_row(self):
        from app.common import bridge
        from app.stores import repository

        inventory_session = _session()
        catalog_session = _session()
        sdb = MagicMock()
        sdb.session.side_effect = [inventory_session, catalog_session]

        result = MagicMock()
        result.selling_price = Decimal("10")
        result.cost_price = Decimal("5")

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", AsyncMock()),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "update_store_product", AsyncMock(return_value=result)
            ),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                block_sale_after_expiry=True,
            )

        stmt = catalog_session.execute.call_args[0][0]
        assert stmt.compile().params["block_sale_after_expiry"] is True

    async def test_a_description_and_unit_edit_reach_the_catalog_row(self):
        from app.common import bridge
        from app.stores import repository

        inventory_session = _session()
        catalog_session = _session()
        sdb = MagicMock()
        sdb.session.side_effect = [inventory_session, catalog_session]

        result = MagicMock()
        result.id = uuid4()
        result.selling_price = Decimal("10")
        result.cost_price = Decimal("5")

        update = AsyncMock(return_value=result)
        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", AsyncMock()),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(repository, "update_store_product", update),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                description="A 50kg bag of jasmine rice",
                unit="kg",
            )

        stmt = catalog_session.execute.call_args[0][0]
        params = stmt.compile().params
        assert params["description"] == "A 50kg bag of jasmine rice"
        assert params["unit"] == "kg"
        # store_products has neither column, so the store row must not be
        # handed fields it cannot store.
        assert "description" not in update.await_args.kwargs
        assert "unit" not in update.await_args.kwargs

    async def test_a_blank_unit_keeps_the_stored_one(self):
        from app.common import bridge
        from app.stores import repository

        inventory_session = _session()
        catalog_session = _session()
        sdb = MagicMock()
        sdb.session.side_effect = [inventory_session, catalog_session]

        result = MagicMock()
        result.id = uuid4()
        result.selling_price = Decimal("10")
        result.cost_price = Decimal("5")

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", AsyncMock()),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "update_store_product", AsyncMock(return_value=result)
            ),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                unit=None,
            )

        stmt = catalog_session.execute.call_args[0][0]
        assert "unit" not in stmt.compile().params

    async def test_an_unknown_category_is_rejected_before_writing(self):
        from app.common import bridge
        from app.stores import repository

        validation_session = _session()
        found = MagicMock()
        found.scalar_one_or_none.return_value = None
        validation_session.execute = AsyncMock(return_value=found)
        sdb = MagicMock()
        sdb.session.return_value = validation_session
        update = AsyncMock()

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(repository, "update_store_product", update),
        ):
            with pytest.raises(ValueError, match="unknown_category_id"):
                await bridge.update_store_product(
                    tenant_id=str(TENANT),
                    store_id=str(STORE),
                    product_id=str(PRODUCT),
                    category_id=str(uuid4()),
                )

        update.assert_not_awaited()

    async def test_an_edit_still_invalidates_the_product_grid(self):
        from app.common import bridge
        from app.stores import repository

        sdb = MagicMock()
        sdb.session.side_effect = [_session(), _session()]
        result = MagicMock()
        result.id = uuid4()
        result.selling_price = Decimal("10")
        result.cost_price = Decimal("5")

        bump = AsyncMock()
        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "cache", MagicMock(delete_pattern=AsyncMock())),
            patch.object(bridge, "scope_bump", bump),
            patch.object(
                repository, "get_store_with_tenant", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "get_store_product", AsyncMock(return_value=MagicMock())
            ),
            patch.object(
                repository, "update_store_product", AsyncMock(return_value=result)
            ),
        ):
            await bridge.update_store_product(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                product_id=str(PRODUCT),
                name="Only a name",
            )

        bump.assert_awaited_once_with(str(TENANT), str(STORE), (bridge.CATALOG,))


class TestExpiredStockCannotBeSold:
    async def test_adding_a_blocked_expired_product_to_a_cart_is_refused(self):
        from datetime import date

        from app.common import bridge

        cart = MagicMock(status="active", tenant_id=TENANT, store_id=None)
        product = MagicMock(
            tenant_id=TENANT,
            block_sale_after_expiry=True,
            expiry_date=date(2020, 1, 1),
        )

        cart_session = _session()
        catalog_session = _session()
        catalog_result = MagicMock()
        catalog_result.scalar_one_or_none.return_value = product
        catalog_session.execute.return_value = catalog_result

        sdb = MagicMock()
        sdb.session.side_effect = [cart_session, catalog_session]

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch(
                "app.cart.repository.get_cart_by_id",
                AsyncMock(return_value=cart),
            ),
        ):
            with pytest.raises(ValueError, match="product_expired"):
                await bridge.add_to_cart(
                    tenant_id=str(TENANT),
                    cart_id=str(uuid4()),
                    product_id=str(PRODUCT),
                )

        cart_session.commit.assert_not_awaited()


class TestCategoryWritesAgeTheRightCache:
    async def test_creating_a_category_bumps_this_tenants_store_catalog(self):
        from app.common import bridge
        from app.catalog import repository

        sdb = MagicMock()
        sdb.session.return_value = _session()
        bump = AsyncMock()

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "scope_bump", bump),
            patch.object(
                repository, "get_category_by_name", AsyncMock(return_value=None)
            ),
            patch.object(repository, "create_category", AsyncMock()),
        ):
            await bridge.create_category(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                name="Drinks",
            )

        bump.assert_awaited_once_with(str(TENANT), str(STORE), (bridge.CATALOG,))

    async def test_updating_a_category_bumps_this_tenants_store_catalog(self):
        from app.common import bridge
        from app.catalog import repository

        sdb = MagicMock()
        sdb.session.return_value = _session()
        existing = MagicMock()
        existing.store_id = STORE
        bump = AsyncMock()

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "scope_bump", bump),
            patch.object(
                repository, "get_category_by_name", AsyncMock(return_value=None)
            ),
            patch.object(
                repository, "update_category", AsyncMock(return_value=existing)
            ),
        ):
            await bridge.update_category(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                category_id=str(uuid4()),
                name="Drinks",
            )

        bump.assert_awaited_once_with(str(TENANT), str(STORE), (bridge.CATALOG,))

    async def test_deleting_a_category_bumps_this_tenants_store_catalog(self):
        from app.common import bridge
        from app.catalog import repository

        session = _session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        sdb = MagicMock()
        sdb.session.return_value = session
        bump = AsyncMock()

        with (
            patch.object(bridge, "_get_sdb", return_value=sdb),
            patch.object(bridge, "scope_bump", bump),
            patch.object(repository, "delete_category", AsyncMock()),
        ):
            await bridge.delete_category(
                tenant_id=str(TENANT),
                store_id=str(STORE),
                category_id=str(uuid4()),
            )

        bump.assert_awaited_once_with(str(TENANT), str(STORE), (bridge.CATALOG,))
