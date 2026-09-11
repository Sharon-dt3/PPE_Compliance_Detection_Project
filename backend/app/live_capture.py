"""RTSP/VMS live-camera-feed capture.

Closes the Phase-2 plan's "integrate to live camera feeds (RTSP/VMS)" item at the ingestion
layer: this module pulls a short clip from a configured ``CameraSource.stream_url`` and
turns it into an ordinary ``MediaJob``, so it runs through the exact same detect/decide/
alert/dashboard pipeline as a manually uploaded file -- no change needed anywhere downstream.

Validated against this environment's OpenCV build (confirmed FFmpeg-backed, so
``cv2.VideoCapture`` genuinely opens ``rtsp://`` URLs) and against real, disclosed failure
cases (an unreachable host). Not validated against a real, currently-live public RTSP demo
stream: the well-known public test endpoints checked during development (an IPVM demo host,
Wowza's official test stream) were respectively no longer resolvable and returning a 403 from
their own server -- both third-party infrastructure issues outside this project, not this
capture code. Real validation still requires either Renewi's own cameras (DPIA-gated) or a
freshly provisioned, currently-live RTSP test server.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import settings
from app.database import SessionLocal
from app.models import CameraSource, JobStatus, MediaJob
from app.platform_settings import get_platform_settings
from app.storage import PrivateMediaStorage

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_REFERENCE = "live-capture-worker"
SYSTEM_ACTOR_ROLE = "system"


class LiveCaptureError(Exception):
    """Raised when a configured live source cannot be opened or yields no frames.

    The message is always generic -- it must never include ``stream_url``, which routinely
    embeds camera credentials, matching this codebase's rule that private connection details
    never reach a log line or API response that a lower-privileged caller could see.
    """


class LiveCaptureConcurrencyLimitError(LiveCaptureError):
    """Raised when the configured concurrent-job limit (FR-ING-03) is already reached.

    Distinct from other `LiveCaptureError` cases so an on-demand caller (see
    `capture_source_now`) can return 429, matching the manual-upload endpoint's own status
    for this identical condition, rather than a generic 502.
    """


def capture_live_clip(stream_url: str, duration_seconds: float, output_path: Path) -> None:
    """Capture ``duration_seconds`` of video from ``stream_url`` and write it to ``output_path``.

    Fails closed: raises ``LiveCaptureError`` rather than silently producing an empty or
    zero-frame file if the source cannot be opened or no frames are actually read, so a
    misconfigured or offline camera can never masquerade as a successfully processed job.
    """
    capture = cv2.VideoCapture(stream_url)
    if not capture.isOpened():
        capture.release()
        raise LiveCaptureError("Could not open the configured live source.")

    native_fps = capture.get(cv2.CAP_PROP_FPS)
    fps = native_fps if native_fps and native_fps > 0 else 15.0
    target_frame_count = max(1, round(duration_seconds * fps))

    writer: cv2.VideoWriter | None = None
    frames_written = 0
    try:
        for _ in range(target_frame_count):
            ok, frame = capture.read()
            if not ok:
                break
            if writer is None:
                height, width = frame.shape[:2]
                writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
            writer.write(frame)
            frames_written += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    if frames_written == 0:
        output_path.unlink(missing_ok=True)
        raise LiveCaptureError("The live source opened but produced no readable frames.")


def queue_live_capture_job(session: Session, source: CameraSource) -> MediaJob:
    """Capture one clip from ``source.stream_url`` and queue it as an ordinary media job.

    Mirrors ``POST /api/v1/media-jobs``'s own job-creation shape exactly (same storage
    layer, same ``expires_at`` policy, same queued status) so every downstream consumer --
    the Celery processing task, the dashboard, retention -- treats a live-captured job
    identically to an uploaded one. Raises ``LiveCaptureError`` if the source is not
    live-enabled or the capture itself fails; the caller decides how to handle that per
    source (see ``capture_live_sources_task``, which isolates one camera's failure from the
    rest of the fleet).
    """
    if not source.stream_url:
        raise LiveCaptureError("This source has no configured live stream URL.")

    in_flight_jobs = session.scalar(
        select(func.count(MediaJob.id)).where(MediaJob.status.in_([JobStatus.QUEUED.value, JobStatus.PROCESSING.value]))
    )
    if in_flight_jobs is not None and in_flight_jobs >= settings.max_concurrent_jobs:
        raise LiveCaptureConcurrencyLimitError("The maximum number of concurrently processing media jobs has been reached.")

    storage = PrivateMediaStorage()
    with tempfile.TemporaryDirectory() as tmp_dir:
        capture_path = Path(tmp_dir) / "capture.mp4"
        capture_live_clip(source.stream_url, settings.live_capture_duration_seconds, capture_path)
        content = capture_path.read_bytes()

    storage_key = storage.save(content, "live-capture.mp4")
    job = MediaJob(
        source_id=source.id,
        zone_id=source.zone_id,
        policy_id=_active_policy_id(session, source),
        filename="live-capture.mp4",
        content_type="video/mp4",
        storage_key=storage_key,
        status=JobStatus.QUEUED.value,
        expires_at=datetime.now(UTC) + timedelta(hours=get_platform_settings(session).raw_media_retention_hours),
    )
    session.add(job)
    session.flush()
    record_audit_event(
        session,
        "media_job.created",
        "media_job",
        job.id,
        SYSTEM_ACTOR_REFERENCE,
        SYSTEM_ACTOR_ROLE,
        "Live-camera capture accepted.",
    )
    return job


@dataclass(frozen=True)
class LiveCaptureRunResult:
    """JSON-safe, non-sensitive summary of one scheduled live-capture sweep."""

    queued_job_ids: tuple[str, ...] = field(default_factory=tuple)
    failed_source_ids: tuple[str, ...] = field(default_factory=tuple)


def capture_live_sources() -> LiveCaptureRunResult:
    """Capture one clip from every enabled, live-configured camera source.

    Each source is attempted independently in its own committed transaction: one camera
    being offline or misconfigured never blocks capture from the rest of the fleet, the
    same isolation principle already used for retention cleanup's per-item processing.
    """
    queued: list[str] = []
    failed: list[str] = []
    with SessionLocal() as session:
        source_ids = session.scalars(
            select(CameraSource.id).where(CameraSource.enabled.is_(True), CameraSource.stream_url.is_not(None))
        ).all()

    for source_id in source_ids:
        with SessionLocal() as session:
            source = session.get(CameraSource, source_id)
            if source is None or not source.stream_url:
                continue
            try:
                job = queue_live_capture_job(session, source)
                session.commit()
                queued.append(job.id)
            except LiveCaptureError:
                logger.warning("Live capture failed for a configured camera source.")
                session.rollback()
                failed.append(source_id)

    return LiveCaptureRunResult(queued_job_ids=tuple(queued), failed_source_ids=tuple(failed))


def _active_policy_id(session: Session, source: CameraSource) -> str:
    """Resolve the zone's currently effective policy, failing closed if none is active."""
    from app.main import _currently_effective_policy  # local import avoids a circular import at module load

    policy = _currently_effective_policy(session, source.zone_id)
    if policy is None:
        raise LiveCaptureError("The source's zone has no currently active safety policy.")
    return policy.id
