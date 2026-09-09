"""Tests for administrator-configurable runtime settings and evidence demo-approval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import CameraSource, ComplianceAlert, EvidenceSnapshot, MediaJob, Zone, ZonePolicy


def _demo_headers(role: str) -> dict[str, str]:
    """Build the local demo-mode authentication header for one POC role."""
    return {"X-Demo-Role": role}


def test_retention_settings_round_trip_and_role_enforcement() -> None:
    """Administrators can change global retention; other roles have read-only or no access."""
    with TestClient(app) as client:
        forbidden = client.get("/api/v1/settings/retention", headers=_demo_headers("safety_supervisor"))
        assert forbidden.status_code == 403

        baseline = client.get("/api/v1/settings/retention", headers=_demo_headers("administrator"))
        assert baseline.status_code == 200

        updated = client.patch(
            "/api/v1/settings/retention",
            headers=_demo_headers("administrator"),
            json={"raw_media_retention_hours": 6, "frame_observation_retention_hours": 48},
        )
        assert updated.status_code == 200
        assert updated.json()["raw_media_retention_hours"] == 6
        assert updated.json()["frame_observation_retention_hours"] == 48

        governance_view = client.get("/api/v1/settings/retention", headers=_demo_headers("governance_reviewer"))
        assert governance_view.status_code == 200
        assert governance_view.json()["raw_media_retention_hours"] == 6

        denied_write = client.patch(
            "/api/v1/settings/retention",
            headers=_demo_headers("governance_reviewer"),
            json={"raw_media_retention_hours": 99},
        )
        assert denied_write.status_code == 403


def test_inference_settings_round_trip_and_role_enforcement() -> None:
    """Administrators can change the detection provider configuration at runtime."""
    with TestClient(app) as client:
        forbidden = client.get("/api/v1/settings/inference", headers=_demo_headers("safety_supervisor"))
        assert forbidden.status_code == 403

        evaluator_view = client.get("/api/v1/settings/inference", headers=_demo_headers("model_evaluator"))
        assert evaluator_view.status_code == 200

        updated = client.patch(
            "/api/v1/settings/inference",
            headers=_demo_headers("administrator"),
            json={"detection_confidence_threshold": 0.4, "hf_model_filename": "alt.pt"},
        )
        assert updated.status_code == 200
        assert updated.json()["detection_confidence_threshold"] == 0.4
        assert updated.json()["hf_model_filename"] == "alt.pt"

        rejected = client.patch(
            "/api/v1/settings/inference",
            headers=_demo_headers("administrator"),
            json={"detection_provider": "not-a-real-provider"},
        )
        assert rejected.status_code == 422


def test_evidence_demo_approval_gates_demonstration_viewer_access() -> None:
    """The demonstration-viewer role sees evidence only after explicit reviewer approval."""
    with TestClient(app) as client:
        with SessionLocal() as session:
            zone = session.query(Zone).first()
            source = session.query(CameraSource).filter_by(zone_id=zone.id).first()
            policy = session.query(ZonePolicy).filter_by(zone_id=zone.id, active=True).first()

            job = MediaJob(
                source_id=source.id,
                zone_id=zone.id,
                policy_id=policy.id,
                filename="clip.jpg",
                content_type="image/jpeg",
                storage_key=f"expired-unused-{uuid4()}",
                status="completed",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add(job)
            session.flush()

            alert = ComplianceAlert(
                job_id=job.id,
                source_id=source.id,
                zone_id=zone.id,
                policy_id=policy.id,
                deduplication_key="test-dedup-key",
                failed_requirement="helmet required",
                confidence=0.9,
                first_observed_at=datetime.now(UTC),
                last_observed_at=datetime.now(UTC),
                evidence_available=True,
                evidence_message="Face-blurred evidence is available.",
            )
            session.add(alert)
            session.flush()

            storage_key = f"{uuid4()}.jpg"
            evidence_root = Path(settings.private_evidence_directory)
            evidence_root.mkdir(parents=True, exist_ok=True)
            (evidence_root / storage_key).write_bytes(b"\xff\xd8\xff\xd9")

            session.add(
                EvidenceSnapshot(
                    alert_id=alert.id,
                    storage_key=storage_key,
                    blurred=True,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            session.commit()
            alert_id = alert.id

        denied = client.get(f"/api/v1/alerts/{alert_id}/evidence", headers=_demo_headers("demonstration_viewer"))
        assert denied.status_code == 403

        approval = client.post(
            f"/api/v1/alerts/{alert_id}/evidence/approve-demo", headers=_demo_headers("safety_supervisor")
        )
        assert approval.status_code == 200

        allowed = client.get(f"/api/v1/alerts/{alert_id}/evidence", headers=_demo_headers("demonstration_viewer"))
        assert allowed.status_code == 200
        assert allowed.content == b"\xff\xd8\xff\xd9"

        still_allowed_for_supervisor = client.get(
            f"/api/v1/alerts/{alert_id}/evidence", headers=_demo_headers("safety_supervisor")
        )
        assert still_allowed_for_supervisor.status_code == 200
