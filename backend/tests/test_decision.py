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
    assert [item.requirement for item in decision.persistent_requirements] == ["helmet required"]
    assert decision.non_compliant_count == 1


def test_missing_positive_ppe_remains_unknown() -> None:
    """A missing helmet detection must not be converted into a violation."""
    frame = FrameDetections(0, (_object("person", 0.9, (0, 0, 100, 200)),))
    decision = SafetyDecisionService().evaluate(DetectionOutcome((frame,), "test", "1"), _policy(persistence_frames=1))

    assert decision.persistence_met is False
    assert decision.non_compliant_count == 0
    assert decision.unknown_count == 1


def test_person_states_for_frame_reports_one_state_per_person_without_identity() -> None:
    """Per-person frame states are ordered by detection only, with no cross-frame identity."""
    frame_objects = (
        _object("person", 0.9, (0, 0, 100, 200)),
        _object("no_helmet", 0.8, (30, 10, 70, 55)),
        _object("person", 0.85, (150, 0, 250, 200)),
        _object("helmet", 0.7, (180, 10, 220, 55)),
    )
    states = SafetyDecisionService().person_states_for_frame(frame_objects, _policy(confidence_threshold=0.5))

    assert len(states) == 2
    assert states[0].compliant is False
    assert states[0].failed_requirement == "helmet required"
    assert states[1].compliant is True
    assert states[1].failed_requirement is None


def test_occlusion_frame_holds_persistence_instead_of_resetting_it() -> None:
    """A frame with no reliable evidence (occlusion) must not reset progress toward an alert.

    Without this, a worker who is genuinely not wearing a helmet but briefly turns away or
    is occluded for one sampled frame could suppress the alert indefinitely, since real
    footage rarely gives back-to-back clean violation frames with zero occlusion.
    """
    violation = FrameDetections(0, (_object("person", 0.9, (0, 0, 100, 200)), _object("no_helmet", 0.8, (30, 10, 70, 55))))
    occluded = FrameDetections(1, (_object("person", 0.9, (0, 0, 100, 200)),))
    outcome = DetectionOutcome((violation, occluded, violation), "test", "1")

    decision = SafetyDecisionService().evaluate(outcome, _policy(persistence_frames=2))

    assert decision.persistence_met is True
    assert [item.requirement for item in decision.persistent_requirements] == ["helmet required"]


def test_confirmed_compliant_frame_resets_persistence() -> None:
    """A frame with confirmed compliant evidence resets progress, unlike an unknown frame."""
    violation = FrameDetections(0, (_object("person", 0.9, (0, 0, 100, 200)), _object("no_helmet", 0.8, (30, 10, 70, 55))))
    compliant = FrameDetections(1, (_object("person", 0.9, (0, 0, 100, 200)), _object("helmet", 0.8, (30, 10, 70, 55))))
    outcome = DetectionOutcome((violation, compliant, violation), "test", "1")

    decision = SafetyDecisionService().evaluate(outcome, _policy(persistence_frames=2))

    assert decision.persistence_met is False


def test_multiple_requirements_can_be_simultaneously_persistent() -> None:
    """A job with both helmet and vest violations reports both, not only the first checked."""
    frame = FrameDetections(
        0,
        (
            _object("person", 0.9, (0, 0, 100, 200)),
            _object("no_helmet", 0.8, (30, 10, 70, 55)),
            _object("no_vest", 0.8, (20, 40, 80, 170)),
        ),
    )
    outcome = DetectionOutcome((frame, frame), "test", "1")
    policy = _policy(vest_required=True, persistence_frames=2)

    decision = SafetyDecisionService().evaluate(outcome, policy)

    reported = {item.requirement for item in decision.persistent_requirements}
    assert reported == {"helmet required", "high-visibility vest required"}


def test_single_strongest_match_is_exclusive_between_people() -> None:
    """One detected box can satisfy only its best-fitting person, not every nearby person."""
    frame_objects = (
        _object("person", 0.9, (0, 0, 100, 200)),
        _object("person", 0.9, (90, 0, 190, 200)),
        _object("no_helmet", 0.8, (70, 10, 100, 55)),
    )
    states = SafetyDecisionService().person_states_for_frame(frame_objects, _policy(confidence_threshold=0.5))

    assert states[0].compliant is False
    assert states[0].failed_requirement == "helmet required"
    assert states[1].compliant is None


def test_too_small_ppe_evidence_is_not_associated() -> None:
    """A PPE box far too small relative to the person is treated as noise, not evidence."""
    frame_objects = (
        _object("person", 0.9, (0, 0, 100, 200)),
        _object("no_helmet", 0.8, (40, 20, 42, 22)),
    )
    states = SafetyDecisionService().person_states_for_frame(frame_objects, _policy(confidence_threshold=0.5))

    assert states[0].compliant is None


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
