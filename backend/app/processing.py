"""Durable media-job processing, conservative safety decisions, and privacy-gated evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import settings
from app.database import SessionLocal
from app.decision import DecisionOutcome, SafetyDecisionService
from app.detection import DetectionOutcome, get_detection_provider
from app.evidence import EvidenceService, PrivacyProcessingError
from app.models import (
    ComplianceAlert,
    EvidenceSnapshot,
    FrameObservation,
    JobStatus,
    MediaJob,
    MetricRollup,
    PersonObservation,
    ZonePolicy,
)
from app.platform_settings import get_platform_settings


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
            detections = get_detection_provider(session).evaluate(media_path)
            decision = SafetyDecisionService().evaluate(detections, policy)
            _persist_outcome(session, job, policy, detections, decision, media_path)
            session.commit()
        except Exception:
            job.status = JobStatus.FAILED.value
            job.message = "Processing failed safely. Review approved model configuration and retry after correction."
            job.completed_at = datetime.now(UTC)
            record_audit_event(
                session,
                "media_job.processing_failed",
                "media_job",
                job.id,
                "processing-worker",
                "system",
                "Safe processing failure.",
            )
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

    _persist_frame_observations(session, job, policy, detections)
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
    record_audit_event(
        session,
        "media_job.completed",
        "media_job",
        job.id,
        "processing-worker",
        "system",
        "Non-identifying aggregate outcome persisted.",
    )

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
        record_audit_event(
            session,
            "alert.deduplicated",
            "compliance_alert",
            alert.id,
            "processing-worker",
            "system",
            "Repeated policy evidence merged.",
        )
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
    record_audit_event(
        session,
        "alert.created",
        "compliance_alert",
        alert.id,
        "processing-worker",
        "system",
        "Persistent non-identifying safety evidence confirmed.",
    )

    try:
        evidence_objects = detections.frames[-1].objects if detections.frames else ()
        storage_key = EvidenceService().create_annotated_blurred_evidence(
            media_path,
            evidence_objects,
            decision.failed_requirement,
        )
    except PrivacyProcessingError:
        alert.evidence_message = "Evidence is unavailable because mandatory privacy processing did not complete."
        record_audit_event(
            session,
            "evidence.blocked",
            "compliance_alert",
            alert.id,
            "processing-worker",
            "system",
            "Mandatory face-blur gate did not complete.",
        )
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
    record_audit_event(
        session,
        "evidence.created",
        "compliance_alert",
        alert.id,
        "processing-worker",
        "system",
        "Face-blurred evidence created.",
    )


def _persist_frame_observations(
    session: Session,
    job: MediaJob,
    policy: ZonePolicy,
    detections: DetectionOutcome,
) -> None:
    """Store one non-identifying frame summary and per-person state for each sampled frame, once.

    Person rows are ephemeral and frame-scoped: ``person_index`` is only the detector's
    per-frame ordinal position, never a tracking key carried across frames or jobs.
    """
    expires_at = datetime.now(UTC) + timedelta(hours=get_platform_settings(session).frame_observation_retention_hours)
    existing_indexes = {
        index
        for index in session.scalars(
            select(FrameObservation.frame_index).where(FrameObservation.job_id == job.id)
        ).all()
    }
    decision_service = SafetyDecisionService()
    for frame in detections.frames:
        if frame.frame_index in existing_indexes:
            continue
        person_states = decision_service.person_states_for_frame(frame.objects, policy)
        compliant = sum(1 for state in person_states if state.compliant is True)
        non_compliant = sum(1 for state in person_states if state.compliant is False)
        unknown = sum(1 for state in person_states if state.compliant is None)
        confidences = tuple(state.confidence for state in person_states if state.compliant is False)
        session.add(
            FrameObservation(
                job_id=job.id,
                frame_index=frame.frame_index,
                person_count=len(person_states),
                compliant_count=compliant,
                non_compliant_count=non_compliant,
                unknown_count=unknown,
                confidence_summary=json.dumps(
                    {
                        "minimum_failure_confidence": round(min(confidences), 3) if confidences else None,
                        "maximum_failure_confidence": round(max(confidences), 3) if confidences else None,
                    }
                ),
                expires_at=expires_at,
            )
        )
        for person_index, state in enumerate(person_states):
            session.add(
                PersonObservation(
                    job_id=job.id,
                    frame_index=frame.frame_index,
                    person_index=person_index,
                    state=_person_state_label(state.compliant),
                    failed_requirement=state.failed_requirement,
                    confidence=state.confidence if state.compliant is False else None,
                    expires_at=expires_at,
                )
            )


def _person_state_label(compliant: bool | None) -> str:
    """Map a tri-state compliance result to its stored, non-identifying state label."""
    if compliant is True:
        return "compliant"
    if compliant is False:
        return "non_compliant"
    return "unknown"
