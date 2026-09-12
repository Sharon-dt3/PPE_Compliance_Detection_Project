"""Durable media-job processing, conservative safety decisions, and privacy-gated evidence."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Protocol

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import settings
from app.database import SessionLocal
from app.decision import DecisionOutcome, RequirementDecision, SafetyDecisionService
from app.detection import DetectedObject, DetectionOutcome, get_detection_provider
from app.evidence import EvidenceService, PrivacyProcessingError
from app.logging_config import get_correlation_id, set_correlation_id
from app.models import (
    CameraSource,
    ComplianceAlert,
    EvidenceSnapshot,
    EventAcknowledgement,
    EventRuleResult,
    FrameObservation,
    JobPreview,
    JobStatus,
    MediaJob,
    MetricRollup,
    PersonObservation,
    ZonePolicy,
)
from app.platform_settings import get_platform_settings, resolve_shift

logger = logging.getLogger(__name__)


class JobRepository(Protocol):
    """Persistence boundary for media-job lifecycle state transitions.

    ``process_media_job`` depends on this interface rather than inline SQLAlchemy calls for
    its queued/processing/failed transitions (Phase 3's SOLID goal): the worker's control
    flow can be exercised against a fake repository, and how a status change is actually
    written has exactly one place to change. The "completed" transition is intentionally
    not split out here -- it is one atomic write together with the frame observations,
    alerts, and metric rollup the same decision produces, in ``_persist_outcome``.
    """

    def get_processable(self, job_id: str) -> MediaJob | None:
        """Return the job only if it is still eligible to be processed, else None."""

    def mark_processing(self, job: MediaJob) -> None:
        """Transition a job to PROCESSING and persist the change immediately."""

    def mark_failed(self, job: MediaJob, message: str) -> None:
        """Transition a job to FAILED, record its completion time, and audit the failure."""


class SqlAlchemyJobRepository:
    """Default ``JobRepository`` backed directly by the active SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        """Bind this repository to the session used for the current job's processing."""
        self._session = session

    def get_processable(self, job_id: str) -> MediaJob | None:
        """Return the job only if it is still eligible to be processed, else None."""
        job = self._session.get(MediaJob, job_id)
        if job is None or job.status not in {JobStatus.QUEUED.value, JobStatus.PROCESSING.value}:
            return None
        return job

    def mark_processing(self, job: MediaJob) -> None:
        """Transition a job to PROCESSING and persist the change immediately."""
        job.status = JobStatus.PROCESSING.value
        job.message = "Processing private media."
        self._session.commit()

    def mark_failed(self, job: MediaJob, message: str) -> None:
        """Transition a job to FAILED, record its completion time, and audit the failure."""
        job.status = JobStatus.FAILED.value
        job.message = message
        job.completed_at = datetime.now(UTC)
        record_audit_event(
            self._session,
            "media_job.processing_failed",
            "media_job",
            job.id,
            "processing-worker",
            "system",
            "Safe processing failure.",
        )
        self._session.commit()


def process_media_job(job_id: str, *, repository_factory: Callable[[Session], JobRepository] = SqlAlchemyJobRepository) -> None:
    """Process one queued private-media job and persist only non-identifying results."""
    previous_correlation_id = get_correlation_id()
    set_correlation_id(job_id)
    try:
        with SessionLocal() as session:
            repository = repository_factory(session)
            job = repository.get_processable(job_id)
            if job is None:
                return

            repository.mark_processing(job)
            logger.info("Media job processing started.")

            try:
                policy = session.get(ZonePolicy, job.policy_id)
                if policy is None:
                    raise RuntimeError("The job policy is unavailable.")
                source = session.get(CameraSource, job.source_id)
                if source is None:
                    raise RuntimeError("The job source is unavailable.")
                effective_policy = _effective_policy(policy, source)
                media_path = str(Path(settings.private_media_directory) / job.storage_key)
                detections = get_detection_provider(session).evaluate(
                    media_path, sampling_fps=effective_policy.sampling_fps
                )
                decision = SafetyDecisionService().evaluate(detections, effective_policy)
                _persist_outcome(session, job, effective_policy, detections, decision, media_path)
                session.commit()
                logger.info("Media job processing completed.")
            except SoftTimeLimitExceeded:
                logger.exception("Media job processing timed out and was failed safely.")
                repository.mark_failed(
                    job,
                    "Processing timed out safely before completion. Retry once the underlying cause "
                    "(for example, an oversized or unusually slow-to-decode file) has been addressed.",
                )
            except Exception:
                logger.exception("Media job processing failed safely.")
                repository.mark_failed(
                    job, "Processing failed safely. Review approved model configuration and retry after correction."
                )
    finally:
        set_correlation_id(previous_correlation_id)


def _effective_policy(policy: ZonePolicy, source: CameraSource) -> SimpleNamespace:
    """Resolve one job's effective policy view (FR-DET-05).

    A source's ``confidence_threshold_override``, when set, replaces the zone policy's flat
    ``confidence_threshold`` for jobs from that source only. Per-class overrides always come
    from the zone policy's ``class_confidence_thresholds_json``. The immutable policy row
    itself is never mutated; this returns a read-only view carrying every attribute decision
    logic and persistence need.
    """
    return SimpleNamespace(
        id=policy.id,
        helmet_required=policy.helmet_required,
        vest_required=policy.vest_required,
        confidence_threshold=(
            source.confidence_threshold_override
            if source.confidence_threshold_override is not None
            else policy.confidence_threshold
        ),
        class_confidence_thresholds=json.loads(policy.class_confidence_thresholds_json or "{}"),
        persistence_frames=policy.persistence_frames,
        deduplication_seconds=policy.deduplication_seconds,
        evidence_retention_hours=policy.evidence_retention_hours,
        sampling_fps=policy.sampling_fps,
    )


def _persist_outcome(
    session: Session,
    job: MediaJob,
    policy: SimpleNamespace,
    detections: DetectionOutcome,
    decision: DecisionOutcome,
    media_path: str,
) -> None:
    """Persist aggregate results and create or update a policy-deduplicated alert.

    ``policy`` here is the source-resolved effective policy view from ``_effective_policy``,
    not the raw ``ZonePolicy`` row.
    """
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
    if not session.scalar(select(JobPreview).where(JobPreview.job_id == job.id)):
        preview_objects = detections.frames[-1].objects if detections.frames else ()
        policy_result = (
            f"{decision.compliant_count} compliant / {decision.non_compliant_count} non-compliant / "
            f"{decision.unknown_count} unknown"
        )
        attempt_job_preview_generation(session, job, media_path, preview_objects, policy_result, policy.evidence_retention_hours)
    if not session.scalar(select(MetricRollup).where(MetricRollup.job_id == job.id)):
        session.add(
            MetricRollup(
                job_id=job.id,
                source_id=job.source_id,
                zone_id=job.zone_id,
                shift=resolve_shift(now, get_platform_settings(session).shift_schedule_json),
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

    # A job can carry more than one simultaneously persistent requirement (e.g. a zone
    # requiring both helmet and vest, both violated) -- each becomes its own independently
    # deduplicated alert rather than only the first one being reported.
    for requirement_decision in decision.persistent_requirements:
        _create_or_update_alert(session, job, policy, detections, media_path, requirement_decision, now)


def _record_rule_result(
    session: Session,
    alert: ComplianceAlert,
    job: MediaJob,
    policy: SimpleNamespace,
    requirement_decision: RequirementDecision,
) -> None:
    """Persist the durable, explainable rule-evaluation record behind one alert touch.

    Written every time a decision reaches or reinforces a persistent requirement, whether
    that creates a new alert or merges into an existing one -- a queryable history of every
    policy version and rule that produced this alert, separate from its current summary.
    """
    session.add(
        EventRuleResult(
            alert_id=alert.id,
            job_id=job.id,
            policy_id=policy.id,
            requirement=requirement_decision.requirement,
            persistent=requirement_decision.persistent,
            non_compliant_count=requirement_decision.non_compliant_count,
            confidence=requirement_decision.confidence,
        )
    )


def _create_or_update_alert(
    session: Session,
    job: MediaJob,
    policy: SimpleNamespace,
    detections: DetectionOutcome,
    media_path: str,
    requirement_decision: RequirementDecision,
    now: datetime,
) -> None:
    """Create one requirement's alert, or merge into its existing deduplication window."""
    deduplication_key = f"{job.source_id}:{policy.id}:{requirement_decision.requirement}"
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
        alert.confidence = max(alert.confidence, requirement_decision.confidence or 0)
        _record_rule_result(session, alert, job, policy, requirement_decision)
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
        failed_requirement=requirement_decision.requirement,
        confidence=requirement_decision.confidence or 0,
        first_observed_at=now,
        last_observed_at=now,
        evidence_available=False,
        evidence_message="Evidence is being prepared by the required privacy gate.",
    )
    session.add(alert)
    session.flush()
    _record_rule_result(session, alert, job, policy, requirement_decision)
    record_audit_event(
        session,
        "alert.created",
        "compliance_alert",
        alert.id,
        "processing-worker",
        "system",
        "Persistent non-identifying safety evidence confirmed.",
    )

    evidence_objects = detections.frames[-1].objects if detections.frames else ()
    attempt_evidence_generation(
        session,
        alert,
        media_path,
        evidence_objects,
        requirement_decision.requirement,
        policy.evidence_retention_hours,
    )


# PUBLIC_INTERFACE
def attempt_evidence_generation(
    session: Session,
    alert: ComplianceAlert,
    media_path: str,
    evidence_objects: tuple[DetectedObject, ...],
    requirement: str,
    evidence_retention_hours: int,
    *,
    actor_reference: str = "processing-worker",
    actor_role: str = "system",
) -> bool:
    """Run the mandatory fail-closed face-blur pipeline and persist evidence, or block safely.

    Shared by normal media-job processing and the controlled evidence-retry endpoint (used
    once the underlying fault -- for example, a misconfigured detector model -- has been
    corrected): both paths must apply the exact same fail-closed sequence and never create
    an accessible evidence object except from a successfully blurred frame.

    Returns:
        True only when face-blurred evidence was created; False when the mandatory privacy
        gate did not complete, in which case the alert is left safely without evidence.
    """
    try:
        storage_key = EvidenceService().create_annotated_blurred_evidence(media_path, evidence_objects, requirement)
    except PrivacyProcessingError:
        alert.evidence_message = "Evidence is unavailable because mandatory privacy processing did not complete."
        record_audit_event(
            session,
            "evidence.blocked",
            "compliance_alert",
            alert.id,
            actor_reference,
            actor_role,
            "Mandatory face-blur gate did not complete.",
        )
        return False

    session.add(
        EvidenceSnapshot(
            alert_id=alert.id,
            storage_key=storage_key,
            blurred=True,
            expires_at=datetime.now(UTC) + timedelta(hours=evidence_retention_hours),
        )
    )
    alert.evidence_available = True
    alert.evidence_message = "Face-blurred evidence is available to authorized reviewers for the configured retention period."
    record_audit_event(
        session,
        "evidence.created",
        "compliance_alert",
        alert.id,
        actor_reference,
        actor_role,
        "Face-blurred evidence created.",
    )
    return True


def attempt_job_preview_generation(
    session: Session,
    job: MediaJob,
    media_path: str,
    preview_objects: tuple[DetectedObject, ...],
    policy_result: str,
    preview_retention_hours: int,
) -> bool:
    """Run the same fail-closed face-blur pipeline for every completed job, not just alerts.

    A job that never crosses the persistence threshold into a confirmed alert still gets a
    reviewable, privacy-safe visual of what the detector actually found -- previously the
    job inspector showed only numeric counts, with no way to see the detection itself.
    Silently does nothing (no row, no exception) if the mandatory privacy gate can't run;
    this is a nice-to-have preview, not a safety-critical evidence record, so it must never
    fail the job itself.
    """
    try:
        storage_key = EvidenceService().create_annotated_blurred_evidence(media_path, preview_objects, policy_result)
    except PrivacyProcessingError:
        record_audit_event(
            session,
            "preview.blocked",
            "media_job",
            job.id,
            "processing-worker",
            "system",
            "Mandatory face-blur gate did not complete; no job preview generated.",
        )
        return False

    session.add(
        JobPreview(
            job_id=job.id,
            storage_key=storage_key,
            blurred=True,
            expires_at=datetime.now(UTC) + timedelta(hours=preview_retention_hours),
        )
    )
    record_audit_event(
        session,
        "preview.created",
        "media_job",
        job.id,
        "processing-worker",
        "system",
        "Face-blurred job preview created.",
    )
    return True


def _persist_frame_observations(
    session: Session,
    job: MediaJob,
    policy: SimpleNamespace,
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
        session.add(
            FrameObservation(
                job_id=job.id,
                frame_index=frame.frame_index,
                person_count=len(person_states),
                compliant_count=compliant,
                non_compliant_count=non_compliant,
                unknown_count=unknown,
                confidence_summary=json.dumps(_class_confidence_summary(frame.objects)),
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


def _class_confidence_summary(objects: tuple[DetectedObject, ...]) -> dict[str, dict[str, float | int]]:
    """Aggregate per-class detection counts and confidence ranges for one frame (FR-DET-03).

    Every normalized label is included, not only compliance-relevant ones: an
    ``unknown_label`` or ``gloves``/``glasses`` detection is preserved here for benchmark
    and evaluation review even though decision.py never acts on it (FR-DET-04). Only
    aggregate counts and confidence ranges are stored — no box geometry or per-object data.
    """
    buckets: dict[str, list[float]] = {}
    for item in objects:
        buckets.setdefault(item.label, []).append(item.confidence)
    return {
        label: {
            "count": len(confidences),
            "min_confidence": round(min(confidences), 3),
            "max_confidence": round(max(confidences), 3),
        }
        for label, confidences in buckets.items()
    }


def _person_state_label(compliant: bool | None) -> str:
    """Map a tri-state compliance result to its stored, non-identifying state label."""
    if compliant is True:
        return "compliant"
    if compliant is False:
        return "non_compliant"
    return "unknown"
