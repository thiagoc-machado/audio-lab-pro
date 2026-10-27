from celery import Celery
from celery.signals import after_setup_logger
from app.core.config import settings
from app.core.logging import configure_logging

celery = Celery(
    "audio_lab",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.audio_tasks", "app.tasks.maintenance"],
)
celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": settings.process_timeout_seconds * 8 + 600},
    result_expires=86400,
    task_track_started=True,
    beat_schedule={
        "dispatch-pending": {"task": "audio.dispatch", "schedule": 5.0},
        "cleanup": {"task": "audio.cleanup", "schedule": 3600.0},
    },
)


@after_setup_logger.connect
def setup_logging(**kwargs):
    configure_logging()
