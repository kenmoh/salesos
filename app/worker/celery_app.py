from celery import Celery
from celery.schedules import crontab

from common.settings import get_common_settings

settings = get_common_settings()

celery_app = Celery(
    "storeflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "storeflow_worker.tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_routes={
        "worker.tasks.task_process_notifications": {"queue": "notifications"},
        "worker.tasks.task_verify_payment": {"queue": "payments"},
        "worker.tasks.task_reconcile_events": {"queue": "sync"},
        "worker.tasks.task_cleanup_sessions": {"queue": "notifications"},
        "worker.tasks.task_check_suspicious_login": {"queue": "notifications"},
        "worker.tasks.task_generate_product_qr": {"queue": "catalog"},
        "worker.tasks.task_refresh_analytics_mvs": {"queue": "analytics"},
    },
    # Beat schedule for periodic tasks
    beat_schedule={
        "process-notifications": {
            "task": "worker.tasks.task_process_notifications",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
        },
        "cleanup-expired-carts": {
            "task": "worker.tasks.task_cleanup_sessions",
            "schedule": crontab(minute=0, hour="*/1"),  # Every hour
        },
        "refresh-analytics": {
            "task": "worker.tasks.task_refresh_analytics_mvs",
            "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
        },
    },
)
