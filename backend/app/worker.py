"""Celery worker configuration for private-media processing and retention."""

import uuid

from celery import Celery

from app.config import settings
from app.logging_config import set_correlation_id
from app.retention import remove_expired_private_data

celery_app = Celery("ppe_compliance", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "remove-expired-private-data-hourly": {
            "task": "ppe.remove_expired_private_data",
            "schedule": 3600.0,
        }
    },
)


@celery_app.task(
    name="ppe.process_media_job",
    soft_time_limit=settings.media_job_soft_time_limit_seconds,
    time_limit=settings.media_job_time_limit_seconds,
)
def process_media_job_task(job_id: str) -> None:
    """Process one persisted job in the background worker.

    ``soft_time_limit`` gives ``process_media_job`` a chance to mark the job FAILED safely
    (it catches ``SoftTimeLimitExceeded`` like any other exception); ``time_limit`` is a
    hard backstop that force-kills the worker process if that cleanup itself hangs -- see
    OPERATIONS.md for the resulting known edge case.
    """
    from app.processing import process_media_job

    process_media_job(job_id)


@celery_app.task(name="ppe.remove_expired_private_data")
def remove_expired_private_data_task() -> dict[str, object]:
    """Delete expired private data and return JSON-safe retention monitoring data."""
    set_correlation_id(f"retention-{uuid.uuid4()}")
    try:
        return remove_expired_private_data().as_monitoring_payload()
    finally:
        set_correlation_id(None)
