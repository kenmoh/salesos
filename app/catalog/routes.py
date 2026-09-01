from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core.responses import ok
from app.common import bridge

scan_router = APIRouter(prefix="/inventory/products", tags=["Products"])


@scan_router.get("/{store_id}/{product_id}")
async def scan_product_lookup(request: Request, store_id: str, product_id: str):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        from starlette.templating import Jinja2Templates
        from pathlib import Path

        templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
        result = await bridge.lookup_product_by_scan(store_id, product_id)
        if not result:
            return HTMLResponse(
                content="<html><body><h2>Product not found</h2></body></html>",
                status_code=404,
            )
        return templates.TemplateResponse(request, "product_scan.html", context=result)

    result = await bridge.lookup_product_by_scan(store_id, product_id)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return ok(result)
