"""Tests for conservative, non-identifying PPE safety decisions."""

from types import SimpleNamespace

from app.decision import SafetyDecisionService
from app.detection import BoundingBox, DetectedObject, DetectionOutcome, FrameDetections


def _object(label: str, confidence: float, box: tuple[float, float, float, float]) -> DetectedObject:
    """Build a test-only detector object."""
    return DetectedObject(label, confidence, BoundingBox(*box))


def _policy(**overrides: object) -> SimpleNamespace:
    """Build the policy attributes used by the decision service."""
    values = {
        "helmet_required": True,
        "vest_required": False,
        "confidence_threshold": 0.5,
        "persistence_frames": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_explicit_associated_negative_requires_persistence() -> None:
    """A persistent, associated explicit no-helmet signal creates a decision."""
    frame = FrameDetections(
        0,
        (
            _object("person", 0.9, (0, 0, 100, 200)),
            _object("no_helmet", 0.8, (30, 10, 70, 55)),
        ),
    )
    outcome = DetectionOutcome((frame, frame, frame), "test", "1")
    decision = SafetyDecisionService().evaluate(outcome, _policy())

    assert decision.persistence_met is True
    assert decision.failed_requirement == "helmet required"
    assert decision.non_compliant_count == 1


def test_missing_positive_ppe_remains_unknown() -> None:
    """A missing helmet detection must not be converted into a violation."""
    frame = FrameDetections(0, (_object("person", 0.9, (0, 0, 100, 200)),))
    decision = SafetyDecisionService().evaluate(DetectionOutcome((frame,), "test", "1"), _policy(persistence_frames=1))

    assert decision.persistence_met is False
    assert decision.non_compliant_count == 0
    assert decision.unknown_count == 1


def test_negative_evidence_outside_person_geometry_is_not_associated() -> None:
    """A nearby but unrelated negative PPE box must not generate a violation."""
    frame = FrameDetections(
        0,
        (
            _object("person", 0.9, (0, 0, 100, 200)),
            _object("no_helmet", 0.9, (220, 0, 280, 50)),
        ),
    )
    decision = SafetyDecisionService().evaluate(DetectionOutcome((frame,), "test", "1"), _policy(persistence_frames=1))

    assert decision.persistence_met is False
    assert decision.unknown_count == 1
