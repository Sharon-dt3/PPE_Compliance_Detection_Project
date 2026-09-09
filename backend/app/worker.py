"""Celery worker configuration for private-media processing and retention."""

from celery import Celery

from app.config import settings
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


@celery_app.task(name="ppe.process_media_job")
def process_media_job_task(job_id: str) -> None:
    """Process one persisted job in the background worker."""
    from app.processing import process_media_job

    process_media_job(job_id)


@celery_app.task(name="ppe.remove_expired_private_data")
def remove_expired_private_data_task() -> dict[str, object]:
    """Delete expired private data and return JSON-safe retention monitoring data."""
    return remove_expired_private_data().as_monitoring_payload()
