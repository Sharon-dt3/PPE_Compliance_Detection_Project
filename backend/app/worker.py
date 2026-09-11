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
        },
        "capture-live-sources": {
            "task": "ppe.capture_live_sources",
            "schedule": settings.live_capture_interval_seconds,
        },
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


@celery_app.task(name="ppe.capture_live_sources")
def capture_live_sources_task() -> dict[str, object]:
    """Capture one clip from every live-enabled camera source and queue it for processing.

    Runs on ``live_capture_interval_seconds``. One camera's failure (offline, unreachable,
    misconfigured URL) is isolated and does not block capture from the rest of the fleet,
    matching this codebase's existing per-item-isolated pattern in retention cleanup.
    """
    from app.live_capture import capture_live_sources

    set_correlation_id(f"live-capture-{uuid.uuid4()}")
    try:
        result = capture_live_sources()
        for job_id in result.queued_job_ids:
            process_media_job_task.delay(job_id)
        return {"sources_captured": len(result.queued_job_ids), "sources_failed": len(result.failed_source_ids)}
    finally:
        set_correlation_id(None)
