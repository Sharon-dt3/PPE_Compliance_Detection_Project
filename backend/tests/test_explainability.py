"""Tests for the durable rule-result and acknowledgement-history records (Phase 3 schema gap).

EventRuleResult and EventAcknowledgement close a real gap: the blueprint's persistence
schema names both as distinct entities, but only summary fields on ComplianceAlert existed
before this -- a second acknowledgement silently overwrote the first with no history.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal, initialise_database
from app.main import app
from app.models import CameraSource, EventRuleResult, JobStatus, MediaJob, Zone, ZonePolicy
from app.processing import process_media_job

initialise_database()


def _demo_headers(role: str) -> dict[str, str]:
    """Build the local demo-mode authentication header for one POC role."""
    return {"X-Demo-Role": role}


def test_event_rule_result_is_recorded_when_processing_creates_an_alert() -> None:
    """A persistent violation writes a durable, explainable EventRuleResult row."""
    suffix = uuid4().hex[:8]
    with SessionLocal() as session:
        zone = Zone(name=f"explainability-zone-{suffix}", description="test")
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
        )
        session.add(policy)
        source = CameraSource(name=f"explainability-source-{suffix}", zone_id=zone.id)
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
        job_id = job.id

    process_media_job(job_id)

    with SessionLocal() as session:
        results = session.query(EventRuleResult).filter_by(job_id=job_id).all()
        assert len(results) == 1
        assert results[0].requirement == "helmet required"
        assert results[0].persistent is True
        assert results[0].non_compliant_count == 1


def test_alert_acknowledgement_and_resolution_build_a_full_history() -> None:
    """Acknowledge then resolve both appear in the acknowledgement history, oldest first."""
    admin = _demo_headers("administrator")
    supervisor = _demo_headers("safety_supervisor")
    with TestClient(app) as client:
        with SessionLocal() as session:
            zone = session.query(Zone).first()
            source = session.query(CameraSource).filter_by(zone_id=zone.id).first()
            policy = session.query(ZonePolicy).filter_by(zone_id=zone.id, active=True).first()
            from app.models import ComplianceAlert

            alert = ComplianceAlert(
                job_id=str(uuid4()),
                source_id=source.id,
                zone_id=zone.id,
                policy_id=policy.id,
                deduplication_key=f"history-test-{uuid4()}",
                failed_requirement="helmet required",
                confidence=0.9,
                first_observed_at=datetime.now(UTC),
                last_observed_at=datetime.now(UTC),
                evidence_available=False,
                evidence_message="Evidence is being prepared by the required privacy gate.",
            )
            session.add(alert)
            session.commit()
            alert_id = alert.id

        acknowledged = client.post(
            f"/api/v1/alerts/{alert_id}/acknowledgements", headers=supervisor, json={"note": "Spoke with site lead."}
        )
        assert acknowledged.status_code == 200

        resolved = client.post(f"/api/v1/alerts/{alert_id}/resolve", headers=supervisor, json={"note": "Helmet issued."})
        assert resolved.status_code == 200

        history = client.get(f"/api/v1/alerts/{alert_id}/acknowledgements", headers=admin)
        assert history.status_code == 200
        entries = history.json()
        assert len(entries) == 2
        assert entries[0]["prior_status"] == "open"
        assert entries[0]["next_status"] == "acknowledged"
        assert entries[1]["prior_status"] == "acknowledged"
        assert entries[1]["next_status"] == "resolved"

        rule_results = client.get(f"/api/v1/alerts/{alert_id}/rule-results", headers=admin)
        assert rule_results.status_code == 200
        assert rule_results.json() == []
