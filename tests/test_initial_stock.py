from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT = "00000000-0000-0000-0000-000000000001"
STORE = "00000000-0000-0000-0000-000000000002"


def _session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_initial_stock_writes_a_history_movement():
    """Opening stock must show on the product history screen."""
    from app.common import bridge
    from app.inventory.models import StockAdjustment, StockMovement

    session = _session()
    sdb = MagicMock()
    sdb.return_value.session.return_value = session

    with (
        patch.object(bridge, "_get_sdb", sdb),
        patch.object(bridge, "taxes_for_ids", AsyncMock(return_value=[])),
        # The QR step uploads to Cloudinary; nothing here needs it.
        patch("app.catalog.cloudinary_upload.upload_qr_png", return_value={}),
    ):
        await bridge.create_product_for_store(
            tenant_id=TENANT,
            store_id=STORE,
            name="Milk",
            selling_price=Decimal("500"),
            qty=12,
        )

    added = [call.args[0] for call in session.add.call_args_list]
    adjustments = [o for o in added if isinstance(o, StockAdjustment)]
    movements = [o for o in added if isinstance(o, StockMovement)]

    assert len(adjustments) == 1
    assert adjustments[0].reason == "initial_stock"

    assert len(movements) == 1
    movement = movements[0]
    assert movement.movement_type == "adjustment"
    assert float(movement.qty_change) == 12
    assert float(movement.balance_before) == 0
    assert float(movement.balance_after) == 12
    assert movement.reason == "initial_stock"
    assert movement.reference_type == "adjustment"
    assert str(movement.product_id) == str(adjustments[0].product_id)
