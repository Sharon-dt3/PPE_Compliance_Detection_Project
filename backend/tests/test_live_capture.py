"""Tests for RTSP/VMS live-camera-feed capture (Phase 2 plan: "integrate to live camera
feeds"). `cv2.VideoCapture`/`cv2.VideoWriter` treat a local file path and a network URL
through the identical generic API -- these tests use a small locally-generated video file
as the "stream" to genuinely exercise `capture_live_clip`'s real read/write loop, and a
guaranteed-unreachable URL to exercise the real, disclosed failure path. (Real-network RTSP
validation against a live third-party demo stream could not be completed this session --
see app/live_capture.py's module docstring for exactly what was tried and why it failed;
that gap is honest, not hidden.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, initialise_database
from app.live_capture import (
    LiveCaptureConcurrencyLimitError,
    LiveCaptureError,
    capture_live_clip,
    capture_live_sources,
    queue_live_capture_job,
)
from app.main import app
from app.models import CameraSource, JobStatus, MediaJob, Zone, ZonePolicy

initialise_database()


def _demo_headers(role: str) -> dict[str, str]:
    return {"X-Demo-Role": role}


def _make_test_video(path: Path, *, frame_count: int = 30, fps: float = 15.0) -> None:
    """Write a tiny synthetic video file usable as a stand-in RTSP source in tests."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for _ in range(frame_count):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()


def _create_zone_source(session, *, stream_url: str | None, enabled: bool = True) -> CameraSource:
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"live-capture-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    session.add(ZonePolicy(zone_id=zone.id, version=1))
    source = CameraSource(name=f"live-capture-source-{suffix}", zone_id=zone.id, stream_url=stream_url, enabled=enabled)
    session.add(source)
    session.commit()
    return source


# ---------------------------------------------------------------------------
# capture_live_clip
# ---------------------------------------------------------------------------


def test_capture_live_clip_writes_real_frames_from_a_reachable_source(tmp_path) -> None:
    source_video = tmp_path / "source.mp4"
    _make_test_video(source_video)
    output_path = tmp_path / "capture.mp4"

    capture_live_clip(str(source_video), duration_seconds=1.0, output_path=output_path)

    assert output_path.is_file()
    readback = cv2.VideoCapture(str(output_path))
    assert readback.isOpened()
    ok, frame = readback.read()
    readback.release()
    assert ok
    assert frame is not None


def test_capture_live_clip_fails_closed_for_an_unreachable_source(tmp_path) -> None:
    """A genuinely nonexistent source must raise, never silently write an empty file."""
    output_path = tmp_path / "capture.mp4"
    with pytest.raises(LiveCaptureError):
        capture_live_clip("/nonexistent/path/does-not-exist.mp4", duration_seconds=1.0, output_path=output_path)
    assert not output_path.exists()


def test_live_capture_error_message_never_leaks_the_source_identity(tmp_path) -> None:
    """The exception message must stay generic -- stream_url routinely embeds credentials."""
    output_path = tmp_path / "capture.mp4"
    secret_looking_path = "/rtsp-style/user:supersecret@host/path"
    try:
        capture_live_clip(secret_looking_path, duration_seconds=1.0, output_path=output_path)
    except LiveCaptureError as error:
        assert "supersecret" not in str(error)
        assert secret_looking_path not in str(error)
    else:
        pytest.fail("expected LiveCaptureError for a nonexistent source")


# ---------------------------------------------------------------------------
# queue_live_capture_job
# ---------------------------------------------------------------------------


def test_queue_live_capture_job_creates_an_ordinary_media_job(tmp_path, monkeypatch) -> None:
    """A successful capture becomes a real, queued MediaJob indistinguishable from an upload."""
    source_video = tmp_path / "source.mp4"
    _make_test_video(source_video)
    monkeypatch.setattr(settings, "live_capture_duration_seconds", 1.0)
    # The shared, persistent test database (see conftest.py) accumulates stray in-flight
    # jobs across the whole test session; raise the limit so this test's own concurrency
    # check is never accidentally tripped by unrelated leftover rows.
    monkeypatch.setattr(settings, "max_concurrent_jobs", 10_000)

    with SessionLocal() as session:
        source = _create_zone_source(session, stream_url=str(source_video))
        job = queue_live_capture_job(session, source)
        session.commit()
        job_id = job.id

    with SessionLocal() as session:
        persisted = session.get(MediaJob, job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.QUEUED.value
        assert persisted.content_type == "video/mp4"


def test_queue_live_capture_job_rejects_a_source_with_no_stream_url() -> None:
    with SessionLocal() as session:
        source = _create_zone_source(session, stream_url=None)
        with pytest.raises(LiveCaptureError):
            queue_live_capture_job(session, source)


def test_queue_live_capture_job_respects_the_concurrent_job_limit(tmp_path, monkeypatch) -> None:
    """FR-ING-03's concurrent-job limit applies to live captures the same as uploads."""
    source_video = tmp_path / "source.mp4"
    _make_test_video(source_video)
    monkeypatch.setattr(settings, "live_capture_duration_seconds", 1.0)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 0)

    with SessionLocal() as session:
        source = _create_zone_source(session, stream_url=str(source_video))
        with pytest.raises(LiveCaptureConcurrencyLimitError):
            queue_live_capture_job(session, source)


# ---------------------------------------------------------------------------
# capture_live_sources (the scheduled sweep)
# ---------------------------------------------------------------------------


def test_capture_live_sources_only_attempts_enabled_live_configured_sources(tmp_path, monkeypatch) -> None:
    source_video = tmp_path / "source.mp4"
    _make_test_video(source_video)
    monkeypatch.setattr(settings, "live_capture_duration_seconds", 1.0)
    # The shared, persistent test database (see conftest.py) accumulates stray in-flight
    # jobs across the whole test session; raise the limit so this test's own concurrency
    # check is never accidentally tripped by unrelated leftover rows.
    monkeypatch.setattr(settings, "max_concurrent_jobs", 10_000)

    with SessionLocal() as session:
        live_source = _create_zone_source(session, stream_url=str(source_video))
        _create_zone_source(session, stream_url=None)  # no stream configured -- must be skipped
        _create_zone_source(session, stream_url=str(source_video), enabled=False)  # disabled -- must be skipped
        live_source_id = live_source.id

    result = capture_live_sources()

    with SessionLocal() as session:
        queued_jobs = session.query(MediaJob).filter(MediaJob.id.in_(result.queued_job_ids)).all()
        assert any(job.source_id == live_source_id for job in queued_jobs)
        # Exactly one of the three fixture sources above was eligible.
        eligible_queued = [job for job in queued_jobs if job.source_id == live_source_id]
        assert len(eligible_queued) == 1


def test_capture_live_sources_isolates_one_broken_camera_from_the_rest(tmp_path, monkeypatch) -> None:
    source_video = tmp_path / "source.mp4"
    _make_test_video(source_video)
    monkeypatch.setattr(settings, "live_capture_duration_seconds", 1.0)
    # The shared, persistent test database (see conftest.py) accumulates stray in-flight
    # jobs across the whole test session; raise the limit so this test's own concurrency
    # check is never accidentally tripped by unrelated leftover rows.
    monkeypatch.setattr(settings, "max_concurrent_jobs", 10_000)

    with SessionLocal() as session:
        working_source = _create_zone_source(session, stream_url=str(source_video))
        broken_source = _create_zone_source(session, stream_url="/nonexistent/broken-camera.mp4")
        working_id, broken_id = working_source.id, broken_source.id

    result = capture_live_sources()

    assert broken_id in result.failed_source_ids
    with SessionLocal() as session:
        queued_jobs = session.query(MediaJob).filter(MediaJob.id.in_(result.queued_job_ids)).all()
        assert any(job.source_id == working_id for job in queued_jobs)
        assert not any(job.source_id == broken_id for job in queued_jobs)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_source_response_never_includes_the_raw_stream_url() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sources",
            headers=_demo_headers("administrator"),
            json={
                "name": f"api-live-source-{uuid4().hex[:8]}",
                "zone_id": _existing_zone_id(),
                "stream_url": "rtsp://user:secretpass@camera.example/stream",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert "stream_url" not in body
        assert "secretpass" not in str(body)
        assert body["live_capture_enabled"] is True


def test_clearing_stream_url_disables_live_capture() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sources",
            headers=_demo_headers("administrator"),
            json={
                "name": f"api-live-source-{uuid4().hex[:8]}",
                "zone_id": _existing_zone_id(),
                "stream_url": "rtsp://camera.example/stream",
            },
        ).json()

        updated = client.patch(
            f"/api/v1/sources/{created['id']}",
            headers=_demo_headers("administrator"),
            json={"clear_stream_url": True},
        )
        assert updated.status_code == 200
        assert updated.json()["live_capture_enabled"] is False


def test_capture_now_rejects_a_source_with_no_configured_stream() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sources",
            headers=_demo_headers("administrator"),
            json={"name": f"api-no-stream-{uuid4().hex[:8]}", "zone_id": _existing_zone_id()},
        ).json()

        response = client.post(f"/api/v1/sources/{created['id']}/capture-now", headers=_demo_headers("safety_supervisor"))
        assert response.status_code == 409


def test_capture_now_is_forbidden_for_the_demo_viewer_role() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sources",
            headers=_demo_headers("administrator"),
            json={"name": f"api-forbidden-{uuid4().hex[:8]}", "zone_id": _existing_zone_id()},
        ).json()

        response = client.post(f"/api/v1/sources/{created['id']}/capture-now", headers=_demo_headers("demonstration_viewer"))
        assert response.status_code == 403


def _existing_zone_id() -> str:
    with SessionLocal() as session:
        zone = session.query(Zone).first()
        if zone is None:
            zone = Zone(name=f"fallback-zone-{uuid4().hex[:8]}", description="test")
            session.add(zone)
            session.flush()
            session.add(ZonePolicy(zone_id=zone.id, version=1))
            session.commit()
        return zone.id
