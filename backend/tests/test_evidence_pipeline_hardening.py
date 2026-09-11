"""Tests for Phase 7's remaining gaps: worker timeout handling and the controlled-retry
mechanism the mandatory face-blur privacy gate requires ("make the job/event eligible for
controlled retry after the fault is fixed").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from celery.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient

from app.database import SessionLocal, initialise_database
from app.main import app
from app.models import CameraSource, JobStatus, MediaJob, Zone, ZonePolicy
from app.processing import process_media_job

initialise_database()


def _demo_headers(role: str) -> dict[str, str]:
    """Build the local demo-mode authentication header for one POC role."""
    return {"X-Demo-Role": role}


def _create_job(session, *, status_value: str, storage_key: str | None = None) -> str:
    """Persist a minimal media job fixture in the given status and return its id."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"retry-test-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    policy = ZonePolicy(zone_id=zone.id, version=1)
    session.add(policy)
    source = CameraSource(name=f"retry-test-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()
    job = MediaJob(
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        filename="clip.jpg",
        content_type="image/jpeg",
        storage_key=storage_key or f"unused-{suffix}",
        status=status_value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(job)
    session.commit()
    return job.id


# ---------------------------------------------------------------------------
# Controlled retry
# ---------------------------------------------------------------------------


def test_supervisor_can_retry_a_failed_job_with_media_still_available() -> None:
    """A failed job whose raw media has not yet expired can be re-queued for retry."""
    with SessionLocal() as session:
        job_id = _create_job(session, status_value=JobStatus.FAILED.value)

    with TestClient(app) as client:
        response = client.post(f"/api/v1/media-jobs/{job_id}/retry", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_retry_is_rejected_for_a_job_that_is_not_failed() -> None:
    """Only a failed job is eligible for the controlled-retry workflow."""
    with SessionLocal() as session:
        job_id = _create_job(session, status_value=JobStatus.COMPLETED.value)

    with TestClient(app) as client:
        response = client.post(f"/api/v1/media-jobs/{job_id}/retry", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 409


def test_retry_is_rejected_once_raw_media_has_already_expired() -> None:
    """A failed job whose raw media was already deleted by retention cannot be retried."""
    with SessionLocal() as session:
        job_id = _create_job(session, status_value=JobStatus.FAILED.value, storage_key=f"expired-{uuid4().hex[:8]}")

    with TestClient(app) as client:
        response = client.post(f"/api/v1/media-jobs/{job_id}/retry", headers=_demo_headers("administrator"))

    assert response.status_code == 409
    assert "already been deleted" in response.json()["detail"]


def test_demo_viewer_cannot_retry_a_failed_job() -> None:
    """Only supervisor or administrator may trigger a retry."""
    with SessionLocal() as session:
        job_id = _create_job(session, status_value=JobStatus.FAILED.value)

    with TestClient(app) as client:
        response = client.post(f"/api/v1/media-jobs/{job_id}/retry", headers=_demo_headers("demonstration_viewer"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Worker soft-time-limit handling
# ---------------------------------------------------------------------------


def test_soft_time_limit_exceeded_fails_the_job_safely(monkeypatch) -> None:
    """A worker soft-timeout is caught and turned into a safe, clearly-labeled failure."""
    with SessionLocal() as session:
        job_id = _create_job(session, status_value=JobStatus.QUEUED.value)

    def _raise_timeout(_session):
        class _TimingOutProvider:
            def evaluate(self, *_args, **_kwargs):
                raise SoftTimeLimitExceeded()

        return _TimingOutProvider()

    monkeypatch.setattr("app.processing.get_detection_provider", _raise_timeout)

    process_media_job(job_id)

    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        assert job.status == JobStatus.FAILED.value
        assert "timed out" in job.message
