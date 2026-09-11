"""Tests for Phase 7's remaining gaps: worker timeout handling and the controlled-retry
mechanism the mandatory face-blur privacy gate requires ("make the job/event eligible for
controlled retry after the fault is fixed").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, initialise_database
from app.main import app
from app.migrations import _upgrade_evidence_blurred_constraint
from app.models import CameraSource, ComplianceAlert, EvidenceSnapshot, JobStatus, MediaJob, Zone, ZonePolicy
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


def _create_alert_with_job(session, *, evidence_available: bool, job_storage_key: str | None = None) -> tuple[str, str]:
    """Persist a job with a real readable evidence frame plus its non-compliance alert."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"evidence-retry-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    policy = ZonePolicy(zone_id=zone.id, version=1, evidence_retention_hours=48)
    session.add(policy)
    source = CameraSource(name=f"evidence-retry-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()

    storage_key = job_storage_key or f"{suffix}.jpg"
    if not storage_key.startswith("expired-"):
        media_root = Path(settings.private_media_directory)
        media_root.mkdir(parents=True, exist_ok=True)
        _, buffer = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
        (media_root / storage_key).write_bytes(buffer.tobytes())

    job = MediaJob(
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        filename="clip.jpg",
        content_type="image/jpeg",
        storage_key=storage_key,
        status=JobStatus.COMPLETED.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(job)
    session.flush()

    now = datetime.now(UTC)
    alert = ComplianceAlert(
        job_id=job.id,
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        deduplication_key=f"{source.id}:{policy.id}:no_helmet",
        failed_requirement="no_helmet",
        confidence=0.9,
        first_observed_at=now,
        last_observed_at=now,
        evidence_available=evidence_available,
        evidence_message="Evidence is being prepared by the required privacy gate.",
    )
    session.add(alert)
    if evidence_available:
        session.flush()
        session.add(
            EvidenceSnapshot(
                alert_id=alert.id,
                storage_key=f"existing-{suffix}.jpg",
                blurred=True,
                expires_at=now + timedelta(hours=48),
            )
        )
    session.commit()
    return alert.id, job.id


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


# ---------------------------------------------------------------------------
# Alert evidence retry (the fail-closed face-blur gate's own controlled retry)
#
# A completed media job whose mandatory face-blur step failed leaves the *job* status as
# "completed" (detection and the compliance decision both succeeded); only the privacy gate
# did not. POST /api/v1/media-jobs/{id}/retry is unreachable for this exact scenario because
# it requires status == "failed". These tests cover the alert-scoped retry that closes that
# gap.
# ---------------------------------------------------------------------------


def test_supervisor_can_retry_evidence_once_the_privacy_gate_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A blocked alert becomes viewable once a working face detector is configured and retried."""
    with SessionLocal() as session:
        alert_id, _ = _create_alert_with_job(session, evidence_available=False)

    prototxt_path = tmp_path / "deploy.prototxt"
    model_path = tmp_path / "model.caffemodel"
    prototxt_path.write_text("placeholder")
    model_path.write_bytes(b"placeholder")
    monkeypatch.setattr(settings, "face_detector_prototxt_path", str(prototxt_path))
    monkeypatch.setattr(settings, "face_detector_model_path", str(model_path))

    class _NoFacesDetector:
        def setInput(self, _blob) -> None:
            pass

        def forward(self):
            return np.zeros((1, 1, 0, 7), dtype=np.float32)

    monkeypatch.setattr(cv2.dnn, "readNetFromCaffe", lambda *_a, **_k: _NoFacesDetector())

    with TestClient(app) as client:
        response = client.post(f"/api/v1/alerts/{alert_id}/evidence/retry", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 200
    body = response.json()
    assert body["evidence_available"] is True
    assert "available" in body["evidence_message"]

    with SessionLocal() as session:
        snapshot = session.query(EvidenceSnapshot).filter_by(alert_id=alert_id).one()
        assert snapshot.blurred is True


def test_evidence_retry_stays_fail_closed_when_the_privacy_gate_still_cannot_run() -> None:
    """A retry attempted before the underlying fault is fixed reports the same safe message."""
    with SessionLocal() as session:
        alert_id, _ = _create_alert_with_job(session, evidence_available=False)

    with TestClient(app) as client:
        response = client.post(f"/api/v1/alerts/{alert_id}/evidence/retry", headers=_demo_headers("administrator"))

    assert response.status_code == 200
    body = response.json()
    assert body["evidence_available"] is False
    assert "did not complete" in body["evidence_message"]

    with SessionLocal() as session:
        assert session.query(EvidenceSnapshot).filter_by(alert_id=alert_id).one_or_none() is None


def test_evidence_retry_is_rejected_once_evidence_already_exists() -> None:
    """An alert that already has evidence is not eligible for retry."""
    with SessionLocal() as session:
        alert_id, _ = _create_alert_with_job(session, evidence_available=True)

    with TestClient(app) as client:
        response = client.post(f"/api/v1/alerts/{alert_id}/evidence/retry", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 409


def test_evidence_retry_is_rejected_once_raw_media_has_expired() -> None:
    """An alert whose underlying raw media was already deleted cannot regenerate evidence."""
    with SessionLocal() as session:
        alert_id, _ = _create_alert_with_job(
            session, evidence_available=False, job_storage_key=f"expired-{uuid4().hex[:8]}"
        )

    with TestClient(app) as client:
        response = client.post(f"/api/v1/alerts/{alert_id}/evidence/retry", headers=_demo_headers("administrator"))

    assert response.status_code == 409
    assert "already been deleted" in response.json()["detail"]


def test_demo_viewer_cannot_retry_alert_evidence() -> None:
    """Only supervisor or administrator may trigger the alert evidence retry."""
    with SessionLocal() as session:
        alert_id, _ = _create_alert_with_job(session, evidence_available=False)

    with TestClient(app) as client:
        response = client.post(f"/api/v1/alerts/{alert_id}/evidence/retry", headers=_demo_headers("demonstration_viewer"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Data-layer enforcement: an evidence record may never exist unblurred
# ---------------------------------------------------------------------------


def test_orm_rejects_constructing_an_unblurred_evidence_snapshot() -> None:
    """The ORM layer refuses to build an evidence record before the DB is ever touched."""
    with pytest.raises(ValueError, match="face-blurring succeeds"):
        EvidenceSnapshot(
            alert_id=str(uuid4()),
            storage_key=f"{uuid4()}.jpg",
            blurred=False,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


def test_database_check_constraint_rejects_an_unblurred_row_bypassing_the_orm() -> None:
    """A raw SQL insert that bypasses ORM validation is still rejected by the DB itself."""
    with SessionLocal() as session:
        alert_id, _ = _create_alert_with_job(session, evidence_available=False)

    with SessionLocal() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO evidence_snapshots (id, alert_id, storage_key, blurred, expires_at) "
                    "VALUES (:id, :alert_id, :storage_key, 0, :expires_at)"
                ),
                {
                    "id": str(uuid4()),
                    "alert_id": alert_id,
                    "storage_key": f"{uuid4()}.jpg",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                },
            )
            session.commit()


def test_migration_upgrades_a_legacy_table_and_drops_any_noncompliant_row(tmp_path) -> None:
    """An existing SQLite database without the constraint is rebuilt safely on upgrade."""
    from sqlalchemy import create_engine

    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE evidence_snapshots ("
                "id VARCHAR(36) PRIMARY KEY, alert_id VARCHAR(36) NOT NULL UNIQUE, "
                "storage_key VARCHAR(255) NOT NULL UNIQUE, blurred BOOLEAN NOT NULL, "
                "expires_at TIMESTAMP NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, "
                "deleted_at TIMESTAMP, demo_approved BOOLEAN DEFAULT 0 NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence_snapshots (id, alert_id, storage_key, blurred, expires_at) "
                "VALUES ('good', 'alert-good', 'good.jpg', 1, '2030-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence_snapshots (id, alert_id, storage_key, blurred, expires_at) "
                "VALUES ('bad', 'alert-bad', 'bad.jpg', 0, '2030-01-01 00:00:00')"
            )
        )

    with engine.begin() as connection:
        _upgrade_evidence_blurred_constraint(connection)

    with engine.begin() as connection:
        remaining = connection.execute(text("SELECT id FROM evidence_snapshots")).scalars().all()
        assert remaining == ["good"]
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO evidence_snapshots (id, alert_id, storage_key, blurred, expires_at) "
                    "VALUES ('bad2', 'alert-bad2', 'bad2.jpg', 0, '2030-01-01 00:00:00')"
                )
            )
