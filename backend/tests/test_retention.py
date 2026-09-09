"""Focused tests for auditable and privacy-safe retention cleanup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from app.retention import remove_expired_private_data_for_session
from app.worker import remove_expired_private_data_task


class _ScalarResult:
    """Small scalar-result fake for deterministic category selection tests."""

    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        """Return configured selected values."""
        return self._values


class _DeleteResult:
    """Small SQL delete result fake exposing an affected row count."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Session:
    """Transaction fake that records audit records and optionally fails frame cleanup."""

    def __init__(
        self,
        jobs: list[object],
        evidence: list[object],
        *,
        frame_rows: int = 0,
        frame_delete_fails: bool = False,
    ) -> None:
        self._scalar_results = [_ScalarResult(jobs), _ScalarResult(evidence)]
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False
        self.frame_rows = frame_rows
        self.frame_delete_fails = frame_delete_fails

    def scalars(self, _: object) -> _ScalarResult:
        """Return prepared category results in retention query order."""
        return self._scalar_results.pop(0)

    def execute(self, _: object) -> _DeleteResult:
        """Return deletion count or simulate an isolated database failure."""
        if self.frame_delete_fails:
            raise SQLAlchemyError("frame cleanup unavailable")
        return _DeleteResult(self.frame_rows)

    def add(self, item: object) -> None:
        """Record append-only audit entities."""
        self.added.append(item)

    def commit(self) -> None:
        """Record completion of the retention transaction."""
        self.committed = True

    def rollback(self) -> None:
        """Record rollback if a final database commit is not possible."""
        self.rolled_back = True


class _Storage:
    """Private-storage fake that can fail independently without revealing paths."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.deleted: list[str] = []

    def delete(self, storage_key: str) -> None:
        """Record an opaque-key deletion or simulate unavailable private storage."""
        if self.should_fail:
            raise OSError("private storage is temporarily unavailable")
        self.deleted.append(storage_key)


def _events(session: _Session) -> list[object]:
    """Return append-only audit event fakes recorded during a cleanup run."""
    return session.added


def test_retention_deletes_expired_media_evidence_and_frame_summaries() -> None:
    """All expired categories are independently deleted and outcomes are audited."""
    now = datetime(2026, 9, 9, tzinfo=UTC)
    job = SimpleNamespace(id="job-1", storage_key="opaque-upload.jpg")
    snapshot = SimpleNamespace(id="evidence-1", storage_key="opaque-evidence.jpg", deleted_at=None)
    session = _Session([job], [snapshot], frame_rows=4)
    media_storage = _Storage()
    evidence_storage = _Storage()

    result = remove_expired_private_data_for_session(
        session,
        now=now,
        media_storage=media_storage,
        evidence_storage=evidence_storage,
    )

    assert result.status == "completed"
    assert result.expired_media_deleted == 1
    assert result.expired_evidence_deleted == 1
    assert result.expired_frame_summaries_deleted == 4
    assert result.failures == 0
    assert media_storage.deleted == ["opaque-upload.jpg"]
    assert evidence_storage.deleted == ["opaque-evidence.jpg"]
    assert job.storage_key == "expired-job-1"
    assert snapshot.deleted_at == now
    assert session.committed is True
    assert {event.event_type for event in _events(session)} >= {
        "retention.media_deleted",
        "retention.evidence_deleted",
        "retention.frame_summaries_deleted",
        "retention.executed",
    }


def test_non_expired_or_previously_removed_records_are_preserved() -> None:
    """Selection excludes non-expired data, and already-expired markers are never retried."""
    now = datetime(2026, 9, 9, tzinfo=UTC)
    previously_deleted_job = SimpleNamespace(id="job-2", storage_key="expired-job-2")
    session = _Session([previously_deleted_job], [], frame_rows=0)
    media_storage = _Storage()

    result = remove_expired_private_data_for_session(
        session,
        now=now,
        media_storage=media_storage,
        evidence_storage=_Storage(),
    )

    assert result.expired_media_deleted == 0
    assert media_storage.deleted == []
    assert previously_deleted_job.storage_key == "expired-job-2"
    # Frame selection is performed against explicit `expires_at <= now`, not created time.
    assert result.expired_frame_summaries_deleted == 0


def test_media_failure_does_not_stop_evidence_or_frame_cleanup() -> None:
    """A failed media deletion leaves retryable state while other categories complete."""
    now = datetime(2026, 9, 9, tzinfo=UTC)
    job = SimpleNamespace(id="job-3", storage_key="opaque-upload.mp4")
    snapshot = SimpleNamespace(id="evidence-3", storage_key="opaque-evidence.jpg", deleted_at=None)
    session = _Session([job], [snapshot], frame_rows=2)
    evidence_storage = _Storage()

    result = remove_expired_private_data_for_session(
        session,
        now=now,
        media_storage=_Storage(should_fail=True),
        evidence_storage=evidence_storage,
    )

    assert result.status == "completed_with_errors"
    assert result.failure_categories == ("media_deletion",)
    assert job.storage_key == "opaque-upload.mp4"
    assert result.expired_evidence_deleted == 1
    assert result.expired_frame_summaries_deleted == 2
    assert evidence_storage.deleted == ["opaque-evidence.jpg"]
    assert any(event.event_type == "retention.failed" and event.entity_type == "media_job" for event in _events(session))


def test_evidence_and_frame_failures_are_audited_without_stopping_media_cleanup() -> None:
    """Failed evidence and frame cleanup outcomes are safe, auditable, and isolated."""
    now = datetime(2026, 9, 9, tzinfo=UTC)
    job = SimpleNamespace(id="job-4", storage_key="opaque-upload.png")
    snapshot = SimpleNamespace(id="evidence-4", storage_key="opaque-evidence.jpg", deleted_at=None)
    session = _Session([job], [snapshot], frame_delete_fails=True)
    media_storage = _Storage()

    result = remove_expired_private_data_for_session(
        session,
        now=now,
        media_storage=media_storage,
        evidence_storage=_Storage(should_fail=True),
    )

    assert result.status == "completed_with_errors"
    assert set(result.failure_categories) == {"evidence_deletion", "frame_summary_deletion"}
    assert result.expired_media_deleted == 1
    assert media_storage.deleted == ["opaque-upload.png"]
    assert snapshot.deleted_at is None
    failed_entities = {event.entity_type for event in _events(session) if event.event_type == "retention.failed"}
    assert failed_entities == {"evidence_snapshot", "frame_observation"}
    assert any(event.event_type == "retention.completed_with_errors" for event in _events(session))


def test_completely_failed_run_is_audited_with_explicit_failed_status() -> None:
    """All failed cleanup categories yield a safe failed aggregate audit outcome."""
    now = datetime(2026, 9, 9, tzinfo=UTC)
    job = SimpleNamespace(id="job-5", storage_key="opaque-upload.mov")
    snapshot = SimpleNamespace(id="evidence-5", storage_key="opaque-evidence.jpg", deleted_at=None)
    session = _Session([job], [snapshot], frame_delete_fails=True)

    result = remove_expired_private_data_for_session(
        session,
        now=now,
        media_storage=_Storage(should_fail=True),
        evidence_storage=_Storage(should_fail=True),
    )

    assert result.status == "failed"
    assert set(result.failure_categories) == {
        "media_deletion",
        "evidence_deletion",
        "frame_summary_deletion",
    }
    assert result.expired_media_deleted == 0
    assert result.expired_evidence_deleted == 0
    assert result.expired_frame_summaries_deleted == 0
    assert any(event.event_type == "retention.failed" and event.entity_type == "retention_run" for event in _events(session))


def test_monitoring_payload_is_json_safe_and_contains_no_sensitive_storage_reference() -> None:
    """Retention results expose only aggregate counts, status, and failure categories."""
    session = _Session([], [], frame_rows=0)
    result = remove_expired_private_data_for_session(
        session,
        now=datetime.now(UTC),
        media_storage=_Storage(),
        evidence_storage=_Storage(),
    )
    payload = result.as_monitoring_payload()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["status"] == "completed"
    forbidden_terms = ("opaque-", "path", "url", "credential", "worker", "identity", "face")
    assert not any(term in json.dumps(payload).lower() for term in forbidden_terms)


def test_celery_task_returns_serializable_monitoring_result(monkeypatch) -> None:
    """The registered Celery task relays the safe status payload without storage data."""
    class _Result:
        """Small result fake matching the worker's public retention contract."""

        def as_monitoring_payload(self) -> dict[str, object]:
            """Return the expected JSON-compatible task payload."""
            return {
                "status": "completed_with_errors",
                "expired_media_deleted": 1,
                "expired_evidence_deleted": 0,
                "expired_frame_summaries_deleted": 2,
                "failures": 1,
                "failure_categories": ["evidence_deletion"],
            }

    monkeypatch.setattr("app.worker.remove_expired_private_data", lambda: _Result())

    payload = remove_expired_private_data_task.run()

    assert payload["status"] == "completed_with_errors"
    assert payload["failures"] == 1
    assert payload["failure_categories"] == ["evidence_deletion"]
    assert json.loads(json.dumps(payload))["expired_frame_summaries_deleted"] == 2
