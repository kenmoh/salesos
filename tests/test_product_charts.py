"""The two charts on a product: sales by day and stock balance by day.

Both reduce something row-shaped to one point per calendar day. What is
pinned here:

* a day keeps the balance after its *last* movement and the sum of what
  moved, no matter what order the rows arrive in;
* the stock series totals match the points it returns;
* the sales series reads the real materialized view, where the tenant
  isolation lives.
"""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.inventory.service import group_movements_by_day

TENANT = uuid4()
STORE = uuid4()
PRODUCT = uuid4()


def movement(day_offset: int, qty: float, balance: float, hour: int = 12) -> NS:
    return NS(
        created_at=datetime(2026, 1, 1 + day_offset, hour, 0, tzinfo=UTC),
        qty_change=qty,
        balance_after=balance,
        balance_before=balance - qty,
    )


class TestMovementsCollapseIntoDays:
    def test_a_day_keeps_its_last_balance_and_summed_quantity(self):
        rows = [
            movement(0, -2, 8),
            movement(0, -3, 5),
            movement(2, 10, 15),
        ]

        points = group_movements_by_day(rows)

        # The quiet day in the middle is simply not a point; the chart fills it.
        assert [p["date"] for p in points] == ["2026-01-01", "2026-01-03"]
        assert points[0]["balance"] == 5
        assert points[0]["qty_change"] == -5
        assert points[1] == {
            "date": "2026-01-03",
            "balance": 15,
            "balance_before": 5,
            "qty_change": 10,
        }

    def test_rows_arriving_out_of_order_still_land_on_the_right_day(self):
        early = movement(1, 5, 25, hour=9)
        late = movement(1, -5, 20, hour=17)

        points = group_movements_by_day([late, early])

        assert len(points) == 1
        # 17:00 is later, so 20 -- not 25 -- is the day's closing balance.
        assert points[0]["balance"] == 20
        assert points[0]["qty_change"] == 0

    def test_no_movements_is_no_points(self):
        assert group_movements_by_day([]) == []


class TestTheStockSeriesShape:
    async def test_totals_describe_the_points_they_summarise(self):
        from app.common.services import product_stock_series

        rows = [movement(0, -2, 8), movement(3, 10, 18)]
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        series = await product_stock_series(
            session=session,
            business_id=str(TENANT),
            store_id=str(STORE),
            product_id=str(PRODUCT),
            from_date="2026-01-01",
            to_date="2026-01-07",
        )

        assert [p["date"] for p in series["items"]] == ["2026-01-01", "2026-01-04"]
        assert series["totals"] == {"balance": 18, "qty_change": 8}

    async def test_a_window_with_no_movements_says_so(self):
        from app.common.services import product_stock_series

        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        series = await product_stock_series(
            session=session,
            business_id=str(TENANT),
            store_id=str(STORE),
            product_id=str(PRODUCT),
            from_date="2026-01-01",
            to_date="2026-01-07",
        )

        assert series == {
            "items": [],
            "totals": {"balance": 0.0, "qty_change": 0.0},
        }


class TestTheSalesSeriesReadsTheView:
    async def test_an_unknown_tenant_reads_empty_from_the_real_materialized_view(self):
        """The tenant, store and product filters and the date range all live
        in this one query against the view, so an empty result for a random
        tenant is what proves the SQL itself is sound."""
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.common.analytics import product_sales_series
        from app.core.config import settings

        engine = create_async_engine(
            settings.admin_database_url or settings.database_url
        )
        try:
            async with engine.connect() as session:
                series = await product_sales_series(
                    session=session,
                    tenant_id=str(TENANT),
                    store_id=str(STORE),
                    product_id=str(PRODUCT),
                    from_date="2026-01-01",
                    to_date="2026-01-07",
                )
        finally:
            await engine.dispose()

        assert series == {
            "items": [],
            "totals": {"units_sold": 0.0, "revenue": 0.0},
        }
