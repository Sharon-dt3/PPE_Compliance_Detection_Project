"""Audited, failure-tolerant retention cleanup for privacy-sensitive POC records."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.database import SessionLocal
from app.models import EvidenceSnapshot, FrameObservation, MediaJob, PersonObservation
from app.storage import PrivateMediaStorage

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_REFERENCE = "retention-worker"
SYSTEM_ACTOR_ROLE = "system"
RetentionStatus = Literal["completed", "completed_with_errors", "failed"]


class EvidenceStorage(Protocol):
    """Minimal private evidence storage contract required by retention cleanup."""

    def delete(self, storage_key: str) -> None:
        """Delete one opaque evidence key without returning storage details."""


@dataclass(frozen=True)
class RetentionResult:
    """JSON-safe, non-sensitive summary produced by one retention execution."""

    status: RetentionStatus
    expired_media_deleted: int = 0
    expired_evidence_deleted: int = 0
    expired_frame_summaries_deleted: int = 0
    expired_person_summaries_deleted: int = 0
    failures: int = 0
    failure_categories: tuple[str, ...] = ()

    def as_monitoring_payload(self) -> dict[str, object]:
        """Return an aggregate result suitable for JSON task-result backends."""
        payload = asdict(self)
        payload["failure_categories"] = list(self.failure_categories)
        return payload


# PUBLIC_INTERFACE
def remove_expired_private_data() -> RetentionResult:
    """Run scheduled privacy retention and return an aggregate monitoring result.

    Raw private media, face-blurred evidence, and frame-level and person-level aggregate
    summaries are processed independently. Aggregate metric rollups and audit records are
    intentionally not removed by this task.
    """
    from app.evidence import EvidenceService

    with SessionLocal() as session:
        return remove_expired_private_data_for_session(session, evidence_storage=EvidenceService())


# PUBLIC_INTERFACE
def remove_expired_private_data_for_session(
    session: Session,
    *,
    now: datetime | None = None,
    media_storage: PrivateMediaStorage | None = None,
    evidence_storage: EvidenceStorage | None = None,
) -> RetentionResult:
    """Execute each retention category independently within a supplied session.

    Args:
        session: Active persistence session used for state transitions and audit records.
        now: Optional comparison time for deterministic testing.
        media_storage: Optional raw private-media adapter.
        evidence_storage: Optional face-blurred evidence adapter.

    Returns:
        A non-identifying aggregate status, deletion counts, and high-level failure
        categories. It never contains storage paths, URLs, media contents, or identities.
    """
    execution_time = now or datetime.now(UTC)
    private_media = media_storage or PrivateMediaStorage()
    if evidence_storage is None:
        from app.evidence import EvidenceService

        private_evidence: EvidenceStorage = EvidenceService()
    else:
        private_evidence = evidence_storage
    failure_categories: list[str] = []

    media_deleted = _remove_expired_media(session, private_media, execution_time, failure_categories)
    evidence_deleted = _remove_expired_evidence(session, private_evidence, execution_time, failure_categories)
    frame_deleted = _remove_expired_frame_summaries(session, execution_time, failure_categories)
    person_deleted = _remove_expired_person_summaries(session, execution_time, failure_categories)

    failures = len(failure_categories)
    status: RetentionStatus
    if failures == 0:
        status = "completed"
    elif media_deleted or evidence_deleted or frame_deleted or person_deleted:
        status = "completed_with_errors"
    else:
        status = "failed"

    result = RetentionResult(
        status=status,
        expired_media_deleted=media_deleted,
        expired_evidence_deleted=evidence_deleted,
        expired_frame_summaries_deleted=frame_deleted,
        expired_person_summaries_deleted=person_deleted,
        failures=failures,
        failure_categories=tuple(failure_categories),
    )
    _record_run_outcome(session, execution_time, result)
    _commit_retention_audit_safely(session, result)
    return result


def _remove_expired_media(
    session: Session,
    storage: PrivateMediaStorage,
    execution_time: datetime,
    failure_categories: list[str],
) -> int:
    """Delete each expired raw object while preserving retry eligibility on failure."""
    deleted_count = 0
    try:
        jobs = session.scalars(
            select(MediaJob).where(MediaJob.expires_at <= execution_time, MediaJob.storage_key.is_not(None))
        ).all()
    except SQLAlchemyError:
        logger.exception("Unable to select expired raw media for retention.")
        failure_categories.append("media_selection")
        return deleted_count

    for job in jobs:
        if job.storage_key.startswith("expired-"):
            continue
        try:
            storage.delete(job.storage_key)
            job.storage_key = f"expired-{job.id}"
            deleted_count += 1
            _record_item_outcome(session, "retention.media_deleted", "media_job", job.id, "Expired private media deleted.")
        except OSError:
            logger.exception("Private raw media deletion failed for retention item.")
            failure_categories.append("media_deletion")
            _record_item_outcome(
                session,
                "retention.failed",
                "media_job",
                job.id,
                "Raw media deletion failed; the item remains eligible for a later retention run.",
            )
    return deleted_count


def _remove_expired_evidence(
    session: Session,
    storage: EvidenceStorage,
    execution_time: datetime,
    failure_categories: list[str],
) -> int:
    """Delete each expired blurred snapshot while ensuring it remains API-inaccessible."""
    deleted_count = 0
    try:
        snapshots = session.scalars(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.expires_at <= execution_time,
                EvidenceSnapshot.deleted_at.is_(None),
            )
        ).all()
    except SQLAlchemyError:
        logger.exception("Unable to select expired evidence for retention.")
        failure_categories.append("evidence_selection")
        return deleted_count

    for snapshot in snapshots:
        try:
            storage.delete(snapshot.storage_key)
            snapshot.deleted_at = execution_time
            deleted_count += 1
            _record_item_outcome(
                session,
                "retention.evidence_deleted",
                "evidence_snapshot",
                snapshot.id,
                "Expired face-blurred evidence deleted.",
            )
        except OSError:
            logger.exception("Face-blurred evidence deletion failed for retention item.")
            failure_categories.append("evidence_deletion")
            _record_item_outcome(
                session,
                "retention.failed",
                "evidence_snapshot",
                snapshot.id,
                "Face-blurred evidence deletion failed; evidence remains inaccessible after expiry.",
            )
    return deleted_count


def _remove_expired_frame_summaries(
    session: Session,
    execution_time: datetime,
    failure_categories: list[str],
) -> int:
    """Delete only frame summaries whose explicit expiry timestamp has elapsed."""
    try:
        result = session.execute(delete(FrameObservation).where(FrameObservation.expires_at <= execution_time))
        deleted_count = max(result.rowcount or 0, 0)
        if deleted_count:
            _record_item_outcome(
                session,
                "retention.frame_summaries_deleted",
                "frame_observation",
                "expired-frame-summaries",
                f"{deleted_count} expired non-identifying frame summary record(s) deleted.",
            )
        return deleted_count
    except SQLAlchemyError:
        logger.exception("Expired frame-summary deletion failed.")
        failure_categories.append("frame_summary_deletion")
        _record_item_outcome(
            session,
            "retention.failed",
            "frame_observation",
            "expired-frame-summaries",
            "Expired frame-summary deletion failed; records remain eligible for a later retention run.",
        )
        return 0


def _remove_expired_person_summaries(
    session: Session,
    execution_time: datetime,
    failure_categories: list[str],
) -> int:
    """Delete only per-person frame observations whose explicit expiry has elapsed."""
    try:
        result = session.execute(delete(PersonObservation).where(PersonObservation.expires_at <= execution_time))
        deleted_count = max(result.rowcount or 0, 0)
        if deleted_count:
            _record_item_outcome(
                session,
                "retention.person_summaries_deleted",
                "person_observation",
                "expired-person-summaries",
                f"{deleted_count} expired non-identifying person summary record(s) deleted.",
            )
        return deleted_count
    except SQLAlchemyError:
        logger.exception("Expired person-summary deletion failed.")
        failure_categories.append("person_summary_deletion")
        _record_item_outcome(
            session,
            "retention.failed",
            "person_observation",
            "expired-person-summaries",
            "Expired person-summary deletion failed; records remain eligible for a later retention run.",
        )
        return 0


def _record_item_outcome(session: Session, event_type: str, entity_type: str, entity_id: str, detail: str) -> None:
    """Append one safe item-level audit event without allowing audit errors to halt cleanup."""
    try:
        record_audit_event(
            session,
            event_type,
            entity_type,
            entity_id,
            SYSTEM_ACTOR_REFERENCE,
            SYSTEM_ACTOR_ROLE,
            detail,
        )
    except Exception:
        logger.exception("Retention audit event could not be queued; cleanup will continue.")


def _record_run_outcome(session: Session, execution_time: datetime, result: RetentionResult) -> None:
    """Append one safe aggregate run outcome, deliberately excluding sensitive references."""
    _record_item_outcome(
        session,
        "retention.executed" if result.status == "completed" else f"retention.{result.status}",
        "retention_run",
        execution_time.isoformat(),
        (
            f"Retention status={result.status}; raw_media_deleted={result.expired_media_deleted}; "
            f"evidence_deleted={result.expired_evidence_deleted}; "
            f"frame_summaries_deleted={result.expired_frame_summaries_deleted}; "
            f"person_summaries_deleted={result.expired_person_summaries_deleted}; "
            f"failures={result.failures}; categories={','.join(result.failure_categories) or 'none'}."
        ),
    )


def _commit_retention_audit_safely(session: Session, result: RetentionResult) -> None:
    """Commit state and audit events while surfacing a safe log when audit persistence fails."""
    try:
        session.commit()
    except SQLAlchemyError:
        logger.exception(
            "Retention state or audit records could not be committed; status=%s failures=%s.",
            result.status,
            result.failures,
        )
        session.rollback()
