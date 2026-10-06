"""Live event stream.

One endpoint, opened per signed-in client. It carries notifications, not data:
see ``app.core.sse`` for why the payload is deliberately not on the wire.
"""

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.dependencies import TenantDep, require_permission
from app.core.sse import broker, stream, tenant_id_of

logger = logging.getLogger("storeflow.sse")

router = APIRouter(prefix="/events", tags=["Events"])

# Paths whose response stays open indefinitely. TimeoutMiddleware caps every
# other request at 30s and must not apply here, or it would sever every stream
# at 30 seconds and the client would reconnect forever.
STREAM_PATH = "/events/stream"


@router.get(
    "/stream",
    # payments:read, not accounting:read: a cashier holds the former and not the
    # latter, and a cashier is precisely who has pending payments to be told
    # about. Gating on accounting:read refused the stream to the one role that
    # needed it most.
    dependencies=[Depends(require_permission("payments:read"))],
)
async def event_stream(request: Request, ctx: TenantDep) -> StreamingResponse:
    """Subscribe to this tenant's notifications.

    Requires a bearer token rather than a cookie: the client reads the body as a
    stream and sends the header explicitly, which an EventSource cannot do.
    """
    tenant_id = tenant_id_of(ctx.user.business_id)
    subscriber = broker.subscribe(tenant_id)

    async def body() -> AsyncIterator[str]:
        try:
            async for frame in stream(broker, subscriber):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            broker.unsubscribe(subscriber)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            # nginx buffers proxied responses by default, which would hold every
            # frame until the buffer filled and make the stream look dead.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )