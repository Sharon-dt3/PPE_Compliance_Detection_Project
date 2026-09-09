"""Durable media-job processing, conservative safety decisions, and privacy-gated evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.decision import DecisionOutcome, SafetyDecisionService
from app.detection import DetectionOutcome, get_detection_provider
from app.evidence import EvidenceService, PrivacyProcessingError
from app.models import AuditEvent, ComplianceAlert, EvidenceSnapshot, JobStatus, MediaJob, MetricRollup, ZonePolicy


def process_media_job(job_id: str) -> None:
    """Process one queued private-media job and persist only non-identifying results."""
    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        if job is None or job.status not in {JobStatus.QUEUED.value, JobStatus.PROCESSING.value}:
            return

        job.status = JobStatus.PROCESSING.value
        job.message = "Processing private media."
        session.commit()

        try:
            policy = session.get(ZonePolicy, job.policy_id)
            if policy is None:
                raise RuntimeError("The job policy is unavailable.")
            media_path = str(Path(settings.private_media_directory) / job.storage_key)
            detections = get_detection_provider().evaluate(media_path)
            decision = SafetyDecisionService().evaluate(detections, policy)
            _persist_outcome(session, job, policy, detections, decision, media_path)
            session.commit()
        except Exception:
            job.status = JobStatus.FAILED.value
            job.message = "Processing failed safely. Review approved model configuration and retry after correction."
            job.completed_at = datetime.now(UTC)
            _audit(session, "media_job.processing_failed", "media_job", job.id, "worker", "Safe processing failure.")
            session.commit()


def _persist_outcome(
    session: Session,
    job: MediaJob,
    policy: ZonePolicy,
    detections: DetectionOutcome,
    decision: DecisionOutcome,
    media_path: str,
) -> None:
    """Persist aggregate results and create or update a policy-deduplicated alert."""
    now = datetime.now(UTC)
    job.compliant_count = decision.compliant_count
    job.non_compliant_count = decision.non_compliant_count
    job.unknown_count = decision.unknown_count
    job.provider_name = detections.provider_name
    job.provider_version = detections.provider_version
    job.status = JobStatus.COMPLETED.value
    job.completed_at = now
    job.message = (
        "Processing complete. Persistent safety evidence was confirmed."
        if decision.persistence_met
        else "Processing complete. No persistent non-compliance evidence was confirmed."
    )

    if not session.scalar(select(MetricRollup).where(MetricRollup.job_id == job.id)):
        session.add(
            MetricRollup(
                job_id=job.id,
                source_id=job.source_id,
                zone_id=job.zone_id,
                observed=decision.compliant_count + decision.non_compliant_count,
                compliant=decision.compliant_count,
                non_compliant=decision.non_compliant_count,
                unknown=decision.unknown_count,
            )
        )
    _audit(session, "media_job.completed", "media_job", job.id, "worker", "Non-identifying aggregate outcome persisted.")

    if not decision.persistence_met or not decision.failed_requirement:
        return

    deduplication_key = f"{job.source_id}:{policy.id}:{decision.failed_requirement}"
    window_start = now - timedelta(seconds=policy.deduplication_seconds)
    alert = session.scalar(
        select(ComplianceAlert)
        .where(
            ComplianceAlert.deduplication_key == deduplication_key,
            ComplianceAlert.last_observed_at >= window_start,
            ComplianceAlert.status.in_(["open", "acknowledged"]),
        )
        .order_by(ComplianceAlert.last_observed_at.desc())
    )
    if alert is not None:
        alert.last_observed_at = now
        alert.occurrence_count += 1
        alert.confidence = max(alert.confidence, decision.confidence or 0)
        _audit(session, "alert.deduplicated", "compliance_alert", alert.id, "worker", "Repeated policy evidence merged.")
        return

    alert = ComplianceAlert(
        job_id=job.id,
        source_id=job.source_id,
        zone_id=job.zone_id,
        policy_id=policy.id,
        deduplication_key=deduplication_key,
        failed_requirement=decision.failed_requirement,
        confidence=decision.confidence or 0,
        first_observed_at=now,
        last_observed_at=now,
        evidence_available=False,
        evidence_message="Evidence is being prepared by the required privacy gate.",
    )
    session.add(alert)
    session.flush()
    _audit(session, "alert.created", "compliance_alert", alert.id, "worker", "Persistent non-identifying safety evidence confirmed.")

    try:
        storage_key = EvidenceService().create_blurred_evidence(media_path)
    except PrivacyProcessingError:
        alert.evidence_message = "Evidence is unavailable because mandatory privacy processing did not complete."
        _audit(session, "evidence.blocked", "compliance_alert", alert.id, "worker", "Mandatory face-blur gate did not complete.")
        return

    session.add(
        EvidenceSnapshot(
            alert_id=alert.id,
            storage_key=storage_key,
            blurred=True,
            expires_at=now + timedelta(hours=policy.evidence_retention_hours),
        )
    )
    alert.evidence_available = True
    alert.evidence_message = "Face-blurred evidence is available to authorized reviewers for the configured retention period."
    _audit(session, "evidence.created", "compliance_alert", alert.id, "worker", "Face-blurred evidence created.")


def _audit(session: Session, event_type: str, entity_type: str, entity_id: str, actor_role: str, detail: str) -> None:
    """Append a non-identifying audit event in the active transaction."""
    session.add(
        AuditEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_role=actor_role,
            detail=detail,
        )
    )
