from fastapi import APIRouter, Depends, HTTPException

from app.core.responses import DataResponse, ok
from app.auth.schemas.responses import ScannedProduct
from app.common import bridge

scan_router = APIRouter(prefix="/inventory/products", tags=["Products"])


@scan_router.get(
    "/{store_id}/{product_id}",
    response_model=DataResponse[ScannedProduct],
)
async def scan_product_lookup(store_id: str, product_id: str):
    result = await bridge.lookup_product_by_scan(store_id, product_id)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return ok(result)
