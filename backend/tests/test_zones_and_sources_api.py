"""Tests for the zones/sources API, including FR-DET-05 threshold configuration.

Regression coverage for a bug found by manual browser testing: list_zones() built
PolicyResponse inline instead of through the shared _policy_response() helper, so it never
picked up confidence_threshold/class_confidence_thresholds and 500'd on every real request
once those became required response fields.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def _demo_headers(role: str) -> dict[str, str]:
    """Build the local demo-mode authentication header for one POC role."""
    return {"X-Demo-Role": role}


def test_list_zones_includes_full_policy_shape() -> None:
    """Every seeded zone's policy response includes the threshold fields, not just a subset."""
    with TestClient(app) as client:
        response = client.get("/api/v1/zones", headers=_demo_headers("administrator"))

    assert response.status_code == 200
    zones = response.json()
    assert zones
    for zone in zones:
        policy = zone["policy"]
        assert set(policy) == {
            "version",
            "helmet_required",
            "vest_required",
            "confidence_threshold",
            "class_confidence_thresholds",
            "persistence_frames",
            "deduplication_seconds",
            "sampling_fps",
        }


def test_policy_version_round_trips_class_confidence_thresholds() -> None:
    """A new policy version's per-class thresholds persist and are returned by list_zones."""
    admin = _demo_headers("administrator")
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/zones", headers=admin, json={"name": f"Threshold API zone {uuid4()}", "description": "test"}
        )
        zone_id = created.json()["id"]

        patched = client.patch(
            f"/api/v1/zones/{zone_id}/policy",
            headers=admin,
            json={
                "helmet_required": True,
                "vest_required": True,
                "confidence_threshold": 0.25,
                "class_confidence_thresholds": {"no_helmet": 0.4, "vest": 0.35},
                "persistence_frames": 3,
                "deduplication_seconds": 60,
                "evidence_retention_hours": 48,
                "active": True,
            },
        )
        assert patched.status_code == 200
        assert patched.json()["class_confidence_thresholds"] == {"no_helmet": 0.4, "vest": 0.35}

        rejected = client.patch(
            f"/api/v1/zones/{zone_id}/policy", headers=admin, json={"class_confidence_thresholds": {"not-a-label": 0.5}}
        )
        assert rejected.status_code == 422

        listed = client.get("/api/v1/zones", headers=admin)
        listed_zone = next(item for item in listed.json() if item["id"] == zone_id)
        assert listed_zone["policy"]["class_confidence_thresholds"] == {"no_helmet": 0.4, "vest": 0.35}


def test_source_confidence_threshold_override_round_trip() -> None:
    """A source's per-source threshold override can be set, listed, and cleared."""
    admin = _demo_headers("administrator")
    with TestClient(app) as client:
        zone = client.post(
            "/api/v1/zones", headers=admin, json={"name": f"Source override zone {uuid4()}", "description": "test"}
        )
        zone_id = zone.json()["id"]

        created = client.post(
            "/api/v1/sources",
            headers=admin,
            json={"name": f"Source override camera {uuid4()}", "zone_id": zone_id, "confidence_threshold_override": 0.6},
        )
        assert created.status_code == 201
        assert created.json()["confidence_threshold_override"] == 0.6
        source_id = created.json()["id"]

        listed = client.get("/api/v1/sources", headers=admin)
        listed_source = next(item for item in listed.json() if item["id"] == source_id)
        assert listed_source["confidence_threshold_override"] == 0.6

        cleared = client.patch(
            f"/api/v1/sources/{source_id}", headers=admin, json={"clear_confidence_threshold_override": True}
        )
        assert cleared.status_code == 200
        assert cleared.json()["confidence_threshold_override"] is None
