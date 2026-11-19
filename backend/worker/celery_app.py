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
    task_acks_late=True,              # Acknowledge only after task succeeds (safe re-delivery on crash/OOM)
    task_reject_on_worker_lost=True,  # Re-queue task if worker is SIGKILL'd (e.g., Docker OOM Killer exit 137)
    task_time_limit=300,              # 5 minutes hard limit per task
    worker_prefetch_multiplier=1,     # Pull 1 task at a time so other tasks stay queued in Redis
    broker_transport_options={"visibility_timeout": 600},  # Re-deliver unacked tasks if container vanishes (> task_time_limit)
    beat_schedule={
        # Periodic worker heartbeat for sub-millisecond health observability
        "worker_heartbeat_every_30_sec": {
            "task": "backend.worker.tasks.heartbeat_task",
            "schedule": 30.0,
        },
        # Decision D8: Bidirectional reconciliation sweeper runs every 10 minutes
        "reconcile_vector_store_every_10_mins": {
            "task": "backend.worker.tasks.reconcile_vector_store_task",
            "schedule": 600.0,  # seconds
        },
    },
)

import time
from celery.signals import heartbeat_sent, worker_ready, worker_shutdown
import redis

# Fast worker health check & non-blocking observability via Redis heartbeat key
@worker_ready.connect
def on_worker_ready(sender=None, **kwargs):
    try:
        r = redis.Redis.from_url(settings.REDIS_URL)
        r.set("cortex:celery:worker_heartbeat", str(time.time()), ex=60)
    except Exception:
        pass


@heartbeat_sent.connect
def on_heartbeat_sent(sender=None, **kwargs):
    try:
        r = redis.Redis.from_url(settings.REDIS_URL)
        r.set("cortex:celery:worker_heartbeat", str(time.time()), ex=60)
    except Exception:
        pass


@worker_shutdown.connect
def on_worker_shutdown(sender=None, **kwargs):
    try:
        r = redis.Redis.from_url(settings.REDIS_URL)
        r.delete("cortex:celery:worker_heartbeat")
    except Exception:
        pass

