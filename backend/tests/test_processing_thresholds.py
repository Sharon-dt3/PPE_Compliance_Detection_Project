"""Tests proving FR-DET-05 per-source and per-class threshold overrides apply end-to-end.

The demo detector always emits a person (confidence 0.96) and an associated no_helmet
box (confidence 0.91), ignoring the media file entirely, so these tests can drive the real
process_media_job() pipeline without a real image or a real model.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import SessionLocal
from app.models import CameraSource, JobStatus, MediaJob, Zone, ZonePolicy
from app.processing import process_media_job


def _create_job(
    session,
    *,
    zone_confidence_threshold: float,
    class_confidence_thresholds: dict[str, float],
    source_override: float | None,
) -> str:
    """Persist a zone/policy/source/job fixture and return the job id."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"threshold-test-zone-{suffix}", description="Fixture zone for threshold tests.")
    session.add(zone)
    session.flush()

    policy = ZonePolicy(
        zone_id=zone.id,
        version=1,
        helmet_required=True,
        vest_required=False,
        confidence_threshold=zone_confidence_threshold,
        class_confidence_thresholds_json=json.dumps(class_confidence_thresholds),
        persistence_frames=1,
    )
    session.add(policy)

    source = CameraSource(
        name=f"threshold-test-source-{suffix}",
        zone_id=zone.id,
        confidence_threshold_override=source_override,
    )
    session.add(source)
    session.flush()

    job = MediaJob(
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        filename="clip.jpg",
        content_type="image/jpeg",
        storage_key=f"unused-{suffix}",
        status=JobStatus.QUEUED.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(job)
    session.commit()
    return job.id


def test_per_class_threshold_rejects_low_confidence_no_helmet_evidence() -> None:
    """A no_helmet-specific threshold above the demo detector's confidence yields unknown."""
    with SessionLocal() as session:
        job_id = _create_job(
            session,
            zone_confidence_threshold=0.25,
            class_confidence_thresholds={"no_helmet": 0.95},
            source_override=None,
        )

    process_media_job(job_id)

    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        assert job.status == JobStatus.COMPLETED.value
        assert job.non_compliant_count == 0
        assert job.unknown_count == 1


def test_per_class_threshold_accepts_matching_no_helmet_evidence() -> None:
    """A no_helmet threshold at or below the demo detector's confidence is honored."""
    with SessionLocal() as session:
        job_id = _create_job(
            session,
            zone_confidence_threshold=0.25,
            class_confidence_thresholds={"no_helmet": 0.5},
            source_override=None,
        )

    process_media_job(job_id)

    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        assert job.status == JobStatus.COMPLETED.value
        assert job.non_compliant_count == 1


def test_source_override_replaces_zone_policy_threshold() -> None:
    """A zone-policy threshold that would reject the no_helmet evidence (0.93 > 0.91) is
    overridden per source (0.5), so the same detector output becomes non-compliant instead
    of unknown."""
    with SessionLocal() as session:
        job_id = _create_job(
            session,
            zone_confidence_threshold=0.93,
            class_confidence_thresholds={},
            source_override=0.5,
        )

    process_media_job(job_id)

    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        assert job.status == JobStatus.COMPLETED.value
        assert job.non_compliant_count == 1


def test_zone_policy_threshold_applies_without_source_override() -> None:
    """Without a source override, the flat zone-policy threshold (0.93 > 0.91) rejects the
    no_helmet evidence while still accepting the person (0.96 > 0.93), yielding unknown."""
    with SessionLocal() as session:
        job_id = _create_job(
            session,
            zone_confidence_threshold=0.93,
            class_confidence_thresholds={},
            source_override=None,
        )

    process_media_job(job_id)

    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        assert job.status == JobStatus.COMPLETED.value
        assert job.non_compliant_count == 0
        assert job.unknown_count == 1
