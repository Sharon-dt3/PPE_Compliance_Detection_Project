"""Tests for the Phase 8 alert-lifecycle gaps closed after a spec audit against
FR-ALERT-02/03 and FR-RPT-04: `expired`/`cancelled` alert states were declared but never
reachable, acknowledgement/resolution notes were mandatory despite the spec calling them
optional, and safety supervisors were locked out of the dashboard/report endpoints entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal, initialise_database
from app.main import app
from app.models import CameraSource, ComplianceAlert, EvidenceSnapshot, EventAcknowledgement, JobStatus, MediaJob, Zone, ZonePolicy
from app.retention import remove_expired_private_data_for_session

initialise_database()


def _demo_headers(role: str) -> dict[str, str]:
    """Build the local demo-mode authentication header for one POC role."""
    return {"X-Demo-Role": role}


def _create_alert(
    session,
    *,
    status: str = "open",
    evidence_available: bool,
    evidence_expires_at: datetime | None = None,
    first_observed_at: datetime | None = None,
    evidence_retention_hours: int = 48,
) -> str:
    """Persist a zone/policy/source/job plus one alert with the given lifecycle state."""
    suffix = uuid4().hex[:8]
    zone = Zone(name=f"alert-lifecycle-zone-{suffix}", description="test")
    session.add(zone)
    session.flush()
    policy = ZonePolicy(zone_id=zone.id, version=1, evidence_retention_hours=evidence_retention_hours)
    session.add(policy)
    source = CameraSource(name=f"alert-lifecycle-source-{suffix}", zone_id=zone.id)
    session.add(source)
    session.flush()
    job = MediaJob(
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        filename="clip.jpg",
        content_type="image/jpeg",
        storage_key=f"unused-{suffix}",
        status=JobStatus.COMPLETED.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(job)
    session.flush()

    observed_at = first_observed_at or datetime.now(UTC)
    alert = ComplianceAlert(
        job_id=job.id,
        source_id=source.id,
        zone_id=zone.id,
        policy_id=policy.id,
        deduplication_key=f"{source.id}:{policy.id}:no_helmet:{suffix}",
        status=status,
        failed_requirement="no_helmet",
        confidence=0.9,
        first_observed_at=observed_at,
        last_observed_at=observed_at,
        evidence_available=evidence_available,
        evidence_message="Evidence is being prepared by the required privacy gate.",
    )
    session.add(alert)
    session.flush()
    if evidence_available:
        session.add(
            EvidenceSnapshot(
                alert_id=alert.id,
                storage_key=f"evidence-{suffix}.jpg",
                blurred=True,
                expires_at=evidence_expires_at or (datetime.now(UTC) + timedelta(hours=48)),
            )
        )
    session.commit()
    return alert.id


# ---------------------------------------------------------------------------
# FR-ALERT-02: expired/cancelled states must actually be reachable
# ---------------------------------------------------------------------------


def test_open_alert_expires_once_its_evidence_snapshot_expires() -> None:
    """An unresolved alert with expired evidence is automatically moved to `expired`.

    Assertions target only this test's own alert, not the retention run's aggregate count --
    the test database is a persistent, shared file across the whole suite (see conftest.py),
    so other tests' leftover rows may also be legitimately expired by the same sweep.
    """
    with SessionLocal() as session:
        alert_id = _create_alert(
            session,
            status="open",
            evidence_available=True,
            evidence_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        remove_expired_private_data_for_session(session, now=datetime.now(UTC))
        session.expire_all()
        alert = session.get(ComplianceAlert, alert_id)

    assert alert.status == "expired"


def test_open_alert_with_unexpired_evidence_is_left_open() -> None:
    """An alert whose evidence has not yet expired is not touched by the expiry sweep."""
    with SessionLocal() as session:
        alert_id = _create_alert(
            session,
            status="open",
            evidence_available=True,
            evidence_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        remove_expired_private_data_for_session(session, now=datetime.now(UTC))
        session.expire_all()
        alert = session.get(ComplianceAlert, alert_id)

    assert alert.status == "open"


def test_alert_with_no_evidence_expires_after_the_policy_retention_window() -> None:
    """An alert that never got evidence expires once its own policy's retention window elapses."""
    with SessionLocal() as session:
        alert_id = _create_alert(
            session,
            status="acknowledged",
            evidence_available=False,
            first_observed_at=datetime.now(UTC) - timedelta(hours=49),
            evidence_retention_hours=48,
        )
        remove_expired_private_data_for_session(session, now=datetime.now(UTC))
        session.expire_all()
        alert = session.get(ComplianceAlert, alert_id)

    assert alert.status == "expired"


def test_alert_with_no_evidence_still_within_the_policy_retention_window_stays_open() -> None:
    """The no-evidence expiry rule respects the policy's own window, not an arbitrary one."""
    with SessionLocal() as session:
        alert_id = _create_alert(
            session,
            status="open",
            evidence_available=False,
            first_observed_at=datetime.now(UTC) - timedelta(hours=1),
            evidence_retention_hours=48,
        )
        remove_expired_private_data_for_session(session, now=datetime.now(UTC))
        session.expire_all()
        alert = session.get(ComplianceAlert, alert_id)

    assert alert.status == "open"


def test_resolved_alert_is_never_auto_expired() -> None:
    """A resolved alert is a completed human review and must never be touched by expiry."""
    with SessionLocal() as session:
        alert_id = _create_alert(
            session,
            status="resolved",
            evidence_available=True,
            evidence_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        remove_expired_private_data_for_session(session, now=datetime.now(UTC))
        session.expire_all()
        alert = session.get(ComplianceAlert, alert_id)

    assert alert.status == "resolved"


def test_supervisor_can_cancel_an_open_alert() -> None:
    """FR-ALERT-02's `cancelled` state is reachable via a real endpoint for false positives."""
    with SessionLocal() as session:
        alert_id = _create_alert(session, status="open", evidence_available=False)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/alerts/{alert_id}/cancel",
            json={"note": "Confirmed false positive on review."},
            headers=_demo_headers("safety_supervisor"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_is_rejected_for_an_already_acknowledged_alert() -> None:
    """Cancellation only applies before a reviewer has started acting on the alert."""
    with SessionLocal() as session:
        alert_id = _create_alert(session, status="acknowledged", evidence_available=False)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/alerts/{alert_id}/cancel",
            json={"note": "Too late to cancel."},
            headers=_demo_headers("safety_supervisor"),
        )

    assert response.status_code == 409


def test_demo_viewer_cannot_cancel_an_alert() -> None:
    """Cancellation is a reviewer action, not available to the read-only demo role."""
    with SessionLocal() as session:
        alert_id = _create_alert(session, status="open", evidence_available=False)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/alerts/{alert_id}/cancel",
            json={"note": "Should not be permitted."},
            headers=_demo_headers("demonstration_viewer"),
        )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# FR-ALERT-03: acknowledgement/resolution notes must be optional
# ---------------------------------------------------------------------------


def test_alert_can_be_acknowledged_with_no_note_at_all() -> None:
    """An empty request body is accepted; the note field is genuinely optional."""
    with SessionLocal() as session:
        alert_id = _create_alert(session, status="open", evidence_available=False)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/alerts/{alert_id}/acknowledgements",
            json={},
            headers=_demo_headers("safety_supervisor"),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "acknowledged"
    assert body["acknowledgement_note"] is None

    with SessionLocal() as session:
        history = session.query(EventAcknowledgement).filter_by(alert_id=alert_id).one()
        assert history.note == ""


def test_alert_can_be_resolved_with_no_note_at_all() -> None:
    """Resolution notes are optional too, matching FR-ALERT-03 exactly."""
    with SessionLocal() as session:
        alert_id = _create_alert(session, status="open", evidence_available=False)

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/alerts/{alert_id}/resolve",
            json={},
            headers=_demo_headers("safety_supervisor"),
        )

    assert response.status_code == 200
    assert response.json()["resolution_note"] is None


# ---------------------------------------------------------------------------
# FR-RPT-04 / role table: supervisors must not be locked out of the dashboard
# ---------------------------------------------------------------------------


def test_supervisor_can_view_the_compliance_report() -> None:
    """Safety supervisors are entitled to view dashboards per the project blueprint's own role table."""
    with TestClient(app) as client:
        response = client.get("/api/v1/reports/compliance", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 200


def test_supervisor_can_view_the_compliance_trend() -> None:
    """Trend data is part of the same dashboard supervisors are entitled to view."""
    with TestClient(app) as client:
        response = client.get("/api/v1/reports/compliance/trend", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 200


def test_supervisor_can_view_alert_metrics() -> None:
    """Alert lifecycle metrics are part of the same dashboard supervisors are entitled to view."""
    with TestClient(app) as client:
        response = client.get("/api/v1/reports/alerts", headers=_demo_headers("safety_supervisor"))

    assert response.status_code == 200
