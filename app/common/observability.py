"""Optional Logfire wiring for the queue worker services.

The FastAPI app already configures Logfire (app/main.py), but the four
VM-run workers (celery worker, celery beat, outbox relay, event
consumer) never configured a tracer. Best effort: if no write token is
present everything degrades to a no-op, and a worker starts regardless.
"""

from __future__ import annotations

from app.common.settings import get_common_settings


def configure_worker_telemetry(service: str) -> bool:
    """Configure Logfire for one worker service. Returns False when disabled."""
    settings = get_common_settings()
    if not settings.logfire_token:
        return False

    import logfire

    logfire.configure(service_name=f"storeflow-{service}", token=settings.logfire_token)
    logfire.instrument_system_metrics()
    return True
