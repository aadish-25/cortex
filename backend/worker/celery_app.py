"""
Celery Application Configuration — Phase 7 Background Workers (Decision D8)

Configures Celery distributed worker and periodic beat scheduler.
Broker & Backend: Redis (REDIS_URL from settings).
"""

from celery import Celery

from backend.core.config import settings

# Initialize Celery app
celery_app = Celery(
    "cortex_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit per task
    worker_prefetch_multiplier=1,
    beat_schedule={
        # Decision D8: Bidirectional reconciliation sweeper runs every 10 minutes
        "reconcile_vector_store_every_10_mins": {
            "task": "backend.worker.tasks.reconcile_vector_store_task",
            "schedule": 600.0,  # seconds
        },
    },
)
