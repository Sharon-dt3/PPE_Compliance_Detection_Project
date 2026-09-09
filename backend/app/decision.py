"""Conservative PPE safety decision logic with no identity or person tracking."""

from __future__ import annotations

from dataclasses import dataclass

from app.detection import BoundingBox, DetectionOutcome, DetectedObject
from app.models import ZonePolicy


@dataclass(frozen=True)
class PersonState:
    """One frame-scoped person's compliance state; carries no cross-frame identity."""

    compliant: bool | None
    failed_requirement: str | None
    confidence: float


@dataclass(frozen=True)
class DecisionOutcome:
    """Non-identifying, policy-evaluated decision result for one media job."""

    compliant_count: int
    non_compliant_count: int
    unknown_count: int
    failed_requirement: str | None
    confidence: float | None
    persistence_met: bool


class SafetyDecisionService:
    """Associate PPE evidence per frame and require consecutive evidence before alerting."""

    _REQUIREMENTS = (
        ("helmet", "no_helmet", "helmet required"),
        ("vest", "no_vest", "high-visibility vest required"),
    )

    def evaluate(self, detections: DetectionOutcome, policy: ZonePolicy) -> DecisionOutcome:
        """Evaluate detector observations using an immutable policy version.

        The service intentionally does not assign identities or track a person between
        frames. Persistence is based only on repeated aggregate safety evidence in
        consecutive sampled frames.
        """
        active_requirements = [
            requirement_definition
            for requirement_definition in self._REQUIREMENTS
            if getattr(policy, f"{requirement_definition[0]}_required")
        ]
        if not active_requirements:
            return DecisionOutcome(0, 0, 0, None, None, False)

        consecutive_failures: dict[str, int] = {description: 0 for _, _, description in active_requirements}
        maximum_failures: dict[str, int] = {description: 0 for _, _, description in active_requirements}
        highest_confidence: dict[str, float] = {description: 0.0 for _, _, description in active_requirements}
        latest_compliant = 0
        latest_unknown = 0

        for frame in detections.frames:
            people = [item for item in frame.objects if item.label == "person" and self._accepted(item, policy)]
            per_requirement_failures = {description: 0 for _, _, description in active_requirements}
            frame_compliant = 0
            frame_unknown = 0

            for person in people:
                person_state = self._person_state(person, frame.objects, active_requirements, policy)
                if person_state[0] is None:
                    frame_unknown += 1
                    continue
                if person_state[0]:
                    frame_compliant += 1
                    continue
                requirement, confidence = person_state[1], person_state[2]
                per_requirement_failures[requirement] += 1
                highest_confidence[requirement] = max(highest_confidence[requirement], confidence)

            latest_compliant = frame_compliant
            latest_unknown = frame_unknown
            for _, _, description in active_requirements:
                if per_requirement_failures[description]:
                    consecutive_failures[description] += 1
                    maximum_failures[description] = max(
                        maximum_failures[description],
                        per_requirement_failures[description],
                    )
                else:
                    consecutive_failures[description] = 0

        persistent = [
            description
            for _, _, description in active_requirements
            if consecutive_failures[description] >= policy.persistence_frames
        ]
        failed_requirement = persistent[0] if persistent else None
        return DecisionOutcome(
            compliant_count=latest_compliant,
            non_compliant_count=maximum_failures[failed_requirement] if failed_requirement else 0,
            unknown_count=latest_unknown,
            failed_requirement=failed_requirement,
            confidence=highest_confidence[failed_requirement] if failed_requirement else None,
            persistence_met=failed_requirement is not None,
        )

    def person_states_for_frame(
        self, objects: tuple[DetectedObject, ...], policy: ZonePolicy
    ) -> tuple[PersonState, ...]:
        """Return one frame-scoped compliance state per detected person, in detector order.

        The returned sequence carries no identity: its order reflects only the detector's
        per-frame output and must never be used to correlate a person across frames or jobs.
        """
        requirements = [
            requirement
            for requirement in self._REQUIREMENTS
            if getattr(policy, f"{requirement[0]}_required")
        ]
        people = [item for item in objects if item.label == "person" and self._accepted(item, policy)]
        states = []
        for person in people:
            compliant, requirement, confidence = self._person_state(person, objects, requirements, policy)
            states.append(PersonState(compliant, requirement, confidence))
        return tuple(states)

    def summarize_frame(self, objects: tuple[DetectedObject, ...], policy: ZonePolicy) -> tuple[int, int, int, int, tuple[float, ...]]:
        """Return a frame-scoped aggregate safety result without an identity or tracking key."""
        states = self.person_states_for_frame(objects, policy)
        compliant = sum(1 for state in states if state.compliant is True)
        non_compliant = sum(1 for state in states if state.compliant is False)
        unknown = sum(1 for state in states if state.compliant is None)
        confidences = tuple(state.confidence for state in states if state.compliant is False)
        return len(states), compliant, non_compliant, unknown, confidences

    def _person_state(
        self,
        person: DetectedObject,
        objects: tuple[DetectedObject, ...],
        requirements: list[tuple[str, str, str]],
        policy: ZonePolicy,
    ) -> tuple[bool | None, str | None, float]:
        """Classify one person conservatively from associated PPE evidence only."""
        all_positive = True
        for positive_label, negative_label, description in requirements:
            positives = [
                item for item in objects
                if item.label == positive_label and self._accepted(item, policy) and self._is_associated(person.box, item.box)
            ]
            negatives = [
                item for item in objects
                if item.label == negative_label and self._accepted(item, policy) and self._is_associated(person.box, item.box)
            ]
            if negatives:
                return False, description, max(item.confidence for item in negatives)
            if not positives:
                all_positive = False

        return (True, None, 0.0) if all_positive else (None, None, 0.0)

    @staticmethod
    def _accepted(item: DetectedObject, policy: ZonePolicy) -> bool:
        """Reject detector evidence below the active policy confidence threshold."""
        return item.confidence >= policy.confidence_threshold

    @staticmethod
    def _is_associated(person: BoundingBox, ppe: BoundingBox) -> bool:
        """Require PPE geometry to overlap the person's expected upper-body region."""
        centre_x = (ppe.left + ppe.right) / 2
        centre_y = (ppe.top + ppe.bottom) / 2
        padded_left = person.left - person.width * 0.1
        padded_right = person.right + person.width * 0.1
        return padded_left <= centre_x <= padded_right and person.top <= centre_y <= person.bottom
