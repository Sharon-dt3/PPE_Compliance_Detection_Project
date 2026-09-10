"""Tests for the four Phase 2 gaps: policy effective window, max video-FPS validation,
concurrent-job limits, and the administrator-approved test-media retention workflow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette import status as http_status

from app.config import settings
from app.database import SessionLocal, initialise_database
from app.main import app
from app.media_validation import validate_video_file
from app.models import CameraSource, JobStatus, MediaJob, Zone, ZonePolicy

# See test_processing_thresholds.py: modules that drive SessionLocal() directly cannot rely
# on another test file's TestClient(app) startup having already created the schema first.
initialise_database()


def _demo_headers(role: str) -> dict[str, str]:
    """Build the local demo-mode authentication header for one POC role."""
    return {"X-Demo-Role": role}


# ---------------------------------------------------------------------------
# Gap 1: Policy effective start/end date fields
# ---------------------------------------------------------------------------


def test_policy_version_round_trips_effective_window() -> None:
    """An administrator-supplied effective start/end window persists and is returned."""
    admin = _demo_headers("administrator")
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/zones", headers=admin, json={"name": f"Effective window zone {uuid4()}", "description": "test"}
        )
        zone_id = created.json()["id"]

        start = datetime(2026, 10, 1, tzinfo=UTC).isoformat()
        end = datetime(2026, 12, 1, tzinfo=UTC).isoformat()
        patched = client.patch(
            f"/api/v1/zones/{zone_id}/policy",
            headers=admin,
            json={
                "helmet_required": True,
                "vest_required": True,
                "confidence_threshold": 0.25,
                "effective_start": start,
                "effective_end": end,
                "active": True,
            },
        )
        assert patched.status_code == 200
        assert patched.json()["effective_start"] is not None
        assert patched.json()["effective_end"] is not None


def test_policy_effective_end_before_start_is_rejected() -> None:
    """An effective_end at or before effective_start is a validation error, not silently accepted."""
    admin = _demo_headers("administrator")
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/zones", headers=admin, json={"name": f"Inverted window zone {uuid4()}", "description": "test"}
        )
        zone_id = created.json()["id"]

        rejected = client.patch(
            f"/api/v1/zones/{zone_id}/policy",
            headers=admin,
            json={
                "effective_start": datetime(2026, 12, 1, tzinfo=UTC).isoformat(),
                "effective_end": datetime(2026, 10, 1, tzinfo=UTC).isoformat(),
            },
        )
        assert rejected.status_code == 422


def test_policy_without_effective_window_still_defaults_to_none() -> None:
    """Omitting the effective window entirely leaves both fields unset (always-effective)."""
    admin = _demo_headers("administrator")
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/zones", headers=admin, json={"name": f"No window zone {uuid4()}", "description": "test"}
        )
        zone_id = created.json()["id"]

        patched = client.patch(f"/api/v1/zones/{zone_id}/policy", headers=admin, json={"active": True})
        assert patched.status_code == 200
        assert patched.json()["effective_start"] is None
        assert patched.json()["effective_end"] is None


# ---------------------------------------------------------------------------
# Gap 2: Explicit maximum video-FPS validation
# ---------------------------------------------------------------------------


def _write_test_video(path: str, fps: float, frame_count: int = 3) -> None:
    """Write a tiny synthetic video at a specific frame rate for FPS-validation tests."""
    import cv2

    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (16, 16))
    try:
        for _ in range(frame_count):
            writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    finally:
        writer.release()


def test_validate_video_file_rejects_fps_above_configured_maximum(tmp_path, monkeypatch) -> None:
    """A video whose native FPS exceeds the configured maximum is rejected explicitly."""
    monkeypatch.setattr(settings, "max_video_fps", 30.0)
    video_path = tmp_path / "too-fast.mp4"
    _write_test_video(str(video_path), fps=120.0)

    with pytest.raises(ValueError, match="frame rate"):
        validate_video_file(str(video_path))


def test_validate_video_file_accepts_fps_within_configured_maximum(tmp_path, monkeypatch) -> None:
    """A video at or below the configured maximum FPS passes validation."""
    monkeypatch.setattr(settings, "max_video_fps", 60.0)
    video_path = tmp_path / "normal-speed.mp4"
    _write_test_video(str(video_path), fps=30.0)

    # Should not raise.
    validate_video_file(str(video_path))


# ---------------------------------------------------------------------------
# Gap 3: Concurrent-job limits
# ---------------------------------------------------------------------------


def _create_in_flight_job(session, *, status: str) -> None:
    """Persist a minimal in-flight job fixture directly for concurrency-limit tests."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"concurrency-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    policy = ZonePolicy(zone_id=zone.id, version=1)
    session.add(policy)
    source = CameraSource(name=f"concurrency-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()
    session.add(
        MediaJob(
            source_id=source.id,
            zone_id=zone.id,
            policy_id=policy.id,
            filename="clip.jpg",
            content_type="image/jpeg",
            storage_key=f"unused-{suffix}",
            status=status,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()


def test_media_job_creation_rejects_uploads_once_concurrency_limit_is_reached(monkeypatch) -> None:
    """Once the configured concurrent-job cap is reached, a new upload is rejected with 429."""
    monkeypatch.setattr(settings, "max_concurrent_jobs", 1)
    admin = _demo_headers("administrator")
    with TestClient(app) as client:
        with SessionLocal() as session:
            _create_in_flight_job(session, status=JobStatus.QUEUED.value)

            zone = Zone(name=f"concurrency-limit-zone-{uuid4().hex[:8]}", description="test")
            session.add(zone)
            session.flush()
            session.add(ZonePolicy(zone_id=zone.id, version=1))
            source = CameraSource(name=f"concurrency-limit-source-{uuid4().hex[:8]}", zone_id=zone.id)
            session.add(source)
            session.commit()
            source_id = source.id

        response = client.post(
            "/api/v1/media-jobs",
            headers=admin,
            params={"source_id": source_id},
            files={"file": ("clip.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9", "image/jpeg")},
        )
        assert response.status_code == http_status.HTTP_429_TOO_MANY_REQUESTS


# ---------------------------------------------------------------------------
# Gap 4: Administrator-approved test-media retention workflow
# ---------------------------------------------------------------------------


def _create_job(session, *, is_test_media: bool) -> str:
    """Persist a minimal media job fixture and return its id."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"test-media-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    policy = ZonePolicy(zone_id=zone.id, version=1)
    session.add(policy)
    source = CameraSource(name=f"test-media-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()
    job = MediaJob(
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        filename="clip.jpg",
        content_type="image/jpeg",
        storage_key=f"unused-{suffix}",
        status=JobStatus.COMPLETED.value,
        is_test_media=is_test_media,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(job)
    session.commit()
    return job.id


def test_administrator_can_approve_extended_retention_for_test_media() -> None:
    """An administrator can extend retention for a job explicitly flagged as test media."""
    admin = _demo_headers("administrator")
    with SessionLocal() as session:
        job_id = _create_job(session, is_test_media=True)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/media-jobs/{job_id}/approve-test-retention",
            headers=admin,
            json={"retention_hours": 72, "justification": "Extended for a scheduled model-evaluation walkthrough."},
        )
        assert response.status_code == 200
        assert response.json()["test_retention_approved"] is True


def test_real_operational_media_is_not_eligible_for_test_retention_approval() -> None:
    """A job not flagged as test media at upload time cannot use this approval workflow."""
    admin = _demo_headers("administrator")
    with SessionLocal() as session:
        job_id = _create_job(session, is_test_media=False)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/media-jobs/{job_id}/approve-test-retention",
            headers=admin,
            json={"retention_hours": 72, "justification": "Attempting to bypass real-media retention."},
        )
        assert response.status_code == 409


def test_non_administrator_cannot_approve_test_media_retention() -> None:
    """Only the administrator role may approve extended test-media retention."""
    supervisor = _demo_headers("safety_supervisor")
    with SessionLocal() as session:
        job_id = _create_job(session, is_test_media=True)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/media-jobs/{job_id}/approve-test-retention",
            headers=supervisor,
            json={"retention_hours": 72, "justification": "Should be denied."},
        )
        assert response.status_code == 403
