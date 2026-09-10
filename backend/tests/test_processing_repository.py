"""Tests proving process_media_job depends on the injected JobRepository interface.

The worker's queued/processing/failed lifecycle transitions must go through whatever
repository is supplied, not hardcoded inline SQLAlchemy calls (Phase 3's SOLID goal) --
these tests inject a fake and assert the fake, not the real database, is what changed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import SessionLocal, initialise_database
from app.models import CameraSource, JobStatus, MediaJob, Zone
from app.processing import process_media_job

# See test_processing_thresholds.py: this module drives SessionLocal() directly, so it
# cannot rely on another test file's TestClient(app) having created the schema first.
initialise_database()


class _FakeJobRepository:
    """Records every lifecycle call it receives instead of writing through a real session."""

    def __init__(self, session) -> None:
        self._session = session
        self.processing_calls: list[str] = []
        self.failed_calls: list[tuple[str, str]] = []

    def get_processable(self, job_id: str):
        return self._session.get(MediaJob, job_id)

    def mark_processing(self, job: MediaJob) -> None:
        self.processing_calls.append(job.id)
        job.status = JobStatus.PROCESSING.value

    def mark_failed(self, job: MediaJob, message: str) -> None:
        self.failed_calls.append((job.id, message))
        job.status = JobStatus.FAILED.value
        job.message = message


def _create_job_with_missing_policy(session) -> str:
    """Persist a job whose policy_id does not resolve, so processing fails deterministically."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"repo-test-zone-{suffix}", description="Fixture zone for repository tests.")
    session.add(zone)
    session.flush()
    source = CameraSource(name=f"repo-test-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()
    job = MediaJob(
        source_id=source.id,
        zone_id=zone.id,
        policy_id="does-not-exist",
        filename="clip.jpg",
        content_type="image/jpeg",
        storage_key=f"unused-{suffix}",
        status=JobStatus.QUEUED.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(job)
    session.commit()
    return job.id


def test_process_media_job_drives_lifecycle_through_the_injected_repository() -> None:
    """Both the processing and failure transitions are observed on the injected fake."""
    with SessionLocal() as session:
        job_id = _create_job_with_missing_policy(session)

    captured: dict[str, _FakeJobRepository] = {}

    def factory(session) -> _FakeJobRepository:
        repository = _FakeJobRepository(session)
        captured["repository"] = repository
        return repository

    process_media_job(job_id, repository_factory=factory)

    repository = captured["repository"]
    assert repository.processing_calls == [job_id]
    assert len(repository.failed_calls) == 1
    assert repository.failed_calls[0][0] == job_id
    assert "failed safely" in repository.failed_calls[0][1]
