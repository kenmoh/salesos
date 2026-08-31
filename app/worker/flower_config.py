"""Flower configuration for Celery monitoring.

Usage:
    celery -A app.worker.celery_app flower --port=5555

Or with uv:
    uv run celery -A app.worker.celery_app flower --port=5555

Dashboard: http://localhost:5555
API: http://localhost:5555/api/workers
"""

from app.common.settings import get_common_settings

settings = get_common_settings()

broker_url = settings.celery_broker_url
result_backend = settings.celery_result_backend

# Flower settings
flower_port = 5555
flower_debug = False
flower_persistent = True
flower_state_db = "flower.db"
flower_max_tasks = 10000
flower_max_workers = 50
flower_auto_refresh = True
flower_auto_refresh_interval = 5

# SSL (uncomment if behind HTTPS proxy)
# flower_ssl_cert = "/path/to/cert.pem"
# flower_ssl_key = "/path/to/key.pem"

# Basic auth (uncomment to enable)
# flower_basic_auth = "user:password"
