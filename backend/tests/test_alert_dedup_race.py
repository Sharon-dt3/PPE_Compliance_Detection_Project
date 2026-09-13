"""Tests for the alert-deduplication race-condition fix.

Two media jobs from the same camera/policy/rule can finish processing at nearly the same
moment (real Celery worker concurrency, or a live-capture burst across sources). Before this
fix, ``_create_or_update_alert`` checked for an existing alert and created a new one if none
was visible -- but each job runs in its own long-lived transaction, so neither can see the
other's still-uncommitted insert, and both create duplicate alerts for what should be one
deduplicated event. This was confirmed with real production data: a burst of 12 same-camera
violations processed by 8 concurrent Celery workers produced 3 duplicate alerts instead of 1.

The fix adds a partial unique index (``ix_compliance_alerts_dedup_active``) so the database
rejects a second concurrent insert, and the losing transaction catches that rejection and
merges into the winner instead of crashing the job.

This test does not spin up real concurrent transactions (SQLite's locking model doesn't
reproduce Postgres's unique-constraint-under-concurrency behavior reliably). Instead it
directly forces the code path a real race triggers: an alert for the exact same
deduplication key already exists and is open, but sits outside the fast-path lookup's dedup
window -- deterministically proving both that the constraint is enforced and that recovery
merges rather than crashes, which is exactly the scenario a genuine race resolves to once
the "winning" transaction has committed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal, initialise_database
from app.models import CameraSource, ComplianceAlert, EventRuleResult, JobStatus, MediaJob, Zone, ZonePolicy
from app.processing import process_media_job

initialise_database()


def _build_zone_source_policy(session, *, deduplication_seconds: int = 60) -> tuple[Zone, CameraSource, ZonePolicy]:
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"dedup-race-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    policy = ZonePolicy(
        zone_id=zone.id,
        version=1,
        helmet_required=True,
        vest_required=False,
        confidence_threshold=0.25,
        class_confidence_thresholds_json="{}",
        persistence_frames=1,
        deduplication_seconds=deduplication_seconds,
    )
    session.add(policy)
    source = CameraSource(name=f"dedup-race-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()
    return zone, source, policy


def test_database_rejects_a_second_active_alert_for_the_same_dedup_key() -> None:
    """The partial unique index itself is enforced -- proves the schema-level fix is real."""
    with SessionLocal() as session:
        zone, source, policy = _build_zone_source_policy(session)
        dedup_key = f"{source.id}:{policy.id}:helmet required"
        session.add(
            ComplianceAlert(
                job_id=str(uuid4()),
                source_id=source.id,
                zone_id=zone.id,
                policy_id=policy.id,
                deduplication_key=dedup_key,
                status="open",
                failed_requirement="helmet required",
                confidence=0.9,
                first_observed_at=datetime.now(UTC),
                last_observed_at=datetime.now(UTC),
                evidence_available=False,
                evidence_message="Evidence is being prepared by the required privacy gate.",
            )
        )
        session.commit()

        session.add(
            ComplianceAlert(
                job_id=str(uuid4()),
                source_id=source.id,
                zone_id=zone.id,
                policy_id=policy.id,
                deduplication_key=dedup_key,
                status="open",
                failed_requirement="helmet required",
                confidence=0.8,
                first_observed_at=datetime.now(UTC),
                last_observed_at=datetime.now(UTC),
                evidence_available=False,
                evidence_message="Evidence is being prepared by the required privacy gate.",
            )
        )
        try:
            session.flush()
            raised = False
        except Exception:
            raised = True
        assert raised, "a second open alert for the same dedup key must be rejected by the database"


def test_a_rejected_concurrent_insert_merges_into_the_stale_winner_instead_of_crashing() -> None:
    """Forces the exact recovery path a real race resolves to: an active alert for this dedup
    key already exists but is outside the fast-path window, so only the unwindowed recovery
    lookup can find it -- proving the earlier, narrower fix (which only re-queried the
    windowed lookup) would have crashed this job instead of merging."""
    with SessionLocal() as session:
        zone, source, policy = _build_zone_source_policy(session, deduplication_seconds=5)
        dedup_key = f"{source.id}:{policy.id}:helmet required"
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        winner = ComplianceAlert(
            job_id=str(uuid4()),
            source_id=source.id,
            zone_id=zone.id,
            policy_id=policy.id,
            deduplication_key=dedup_key,
            status="open",
            failed_requirement="helmet required",
            confidence=0.7,
            occurrence_count=1,
            first_observed_at=long_ago,
            last_observed_at=long_ago,
            evidence_available=False,
            evidence_message="Evidence is being prepared by the required privacy gate.",
        )
        session.add(winner)
        session.commit()
        winner_id = winner.id

        job = MediaJob(
            source_id=source.id,
            zone_id=zone.id,
            policy_id=policy.id,
            filename="clip.jpg",
            content_type="image/jpeg",
            storage_key=f"unused-{uuid4()}",
            status=JobStatus.QUEUED.value,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    process_media_job(job_id)

    with SessionLocal() as session:
        job = session.get(MediaJob, job_id)
        assert job.status == "completed", "the job must complete safely, not fail, when its alert insert is rejected"

        alerts = session.scalars(select(ComplianceAlert).where(ComplianceAlert.deduplication_key == dedup_key)).all()
        assert len(alerts) == 1, "no duplicate alert should be created -- the rejected insert must merge, not fork"
        assert alerts[0].id == winner_id
        assert alerts[0].occurrence_count == 2
        last_observed = alerts[0].last_observed_at.replace(tzinfo=UTC) if alerts[0].last_observed_at.tzinfo is None else alerts[0].last_observed_at
        assert last_observed > long_ago

        rule_results = session.scalars(select(EventRuleResult).where(EventRuleResult.job_id == job_id)).all()
        assert len(rule_results) == 1
        assert rule_results[0].alert_id == winner_id
