"""Conservative PPE safety decision logic with no identity or person tracking.

Implements the FR-RULE-01..09 person-PPE association and persistence algorithm:
filter by confidence threshold -> anchor selection (person) -> per-rule region definition
-> PPE search within that region -> containment scoring -> single strongest, mutually
exclusive match per rule -> explicit-negative precedence -> per-person state combination
-> frame-level aggregation -> a persistence state machine that only resets on a confirmed
compliant frame, not merely an unknown one (so a brief occlusion mid-violation does not
restart the count towards an alert).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.detection import BoundingBox, DetectionOutcome, DetectedObject
from app.models import ZonePolicy


class RequirementStatus(str, Enum):
    """One person's frame-scoped status for a single PPE rule."""

    COMPLIANT = "compliant"
    VIOLATION = "violation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _PersonRequirementResult:
    """One person's frame-scoped result for one PPE rule; confidence is set only for a violation."""

    person_index: int
    status: RequirementStatus
    confidence: float


@dataclass(frozen=True)
class PersonState:
    """One frame-scoped person's overall compliance state; carries no cross-frame identity.

    Combines every active requirement for this person: any confirmed violation makes the
    person non-compliant (the first violated requirement in policy order is reported);
    otherwise the person is compliant only if every requirement was confirmed compliant,
    and unknown if at least one requirement had no reliable evidence either way.
    """

    compliant: bool | None
    failed_requirement: str | None
    confidence: float


@dataclass(frozen=True)
class RequirementDecision:
    """One PPE rule's persistence-evaluated outcome across a job's sampled frames."""

    requirement: str
    persistent: bool
    non_compliant_count: int
    confidence: float | None


@dataclass(frozen=True)
class DecisionOutcome:
    """Non-identifying, policy-evaluated decision result for one media job.

    ``requirement_decisions`` holds one entry per active policy requirement, so a job where
    a zone requires both helmet and vest can report both as independently persistent -- it
    is not limited to a single violation type per job.
    """

    compliant_count: int
    non_compliant_count: int
    unknown_count: int
    requirement_decisions: tuple[RequirementDecision, ...]

    @property
    def persistence_met(self) -> bool:
        """Return whether at least one requirement reached persistent violation."""
        return any(decision.persistent for decision in self.requirement_decisions)

    @property
    def persistent_requirements(self) -> tuple[RequirementDecision, ...]:
        """Return only the requirements that reached persistent violation this job."""
        return tuple(decision for decision in self.requirement_decisions if decision.persistent)


class SafetyDecisionService:
    """Associate PPE evidence per frame and require consecutive evidence before alerting."""

    _REQUIREMENTS = (
        ("helmet", "no_helmet", "helmet required"),
        ("vest", "no_vest", "high-visibility vest required"),
    )

    # FR-RULE-02: each rule's PPE search region, as a (top_fraction, bottom_fraction) of the
    # person's own box height. Configured here in code rather than per-policy: these regions
    # describe detector geometry (where a helmet vs a vest can physically appear on a person),
    # not a site-specific safety rule, so they do not belong in the same per-zone configuration
    # surface as thresholds. A rule with no entry falls back to the full person height.
    _REGIONS: dict[str, tuple[float, float]] = {
        "helmet": (0.0, 0.45),
        "vest": (0.15, 0.9),
    }
    _DEFAULT_REGION = (0.0, 1.0)
    _HORIZONTAL_PADDING_RATIO = 0.1

    # FR-RULE-03/05: association thresholds. A candidate must have at least this fraction of
    # its own box contained within the person's rule region, and must be at least this large
    # relative to the person's box -- a detection far smaller than that is more likely sensor
    # noise or an unrelated small object than a real piece of PPE on this specific person.
    _MIN_CONTAINMENT_RATIO = 0.5
    _MIN_RELATIVE_PPE_AREA = 0.002

    def evaluate(self, detections: DetectionOutcome, policy: ZonePolicy) -> DecisionOutcome:
        """Evaluate detector observations using an immutable policy version.

        The service intentionally does not assign identities or track a person between
        frames. Persistence is a per-requirement state machine driven only by each sampled
        frame's own evidence: a frame with a confirmed violation advances it, a frame with
        confirmed compliance resets it, and a frame with no reliable evidence for that
        requirement (occlusion, no person detected, ambiguous association) holds it
        unchanged -- so a brief occlusion mid-violation cannot suppress an alert by
        resetting progress that a truly compliant frame would.
        """
        requirements = [
            requirement_definition
            for requirement_definition in self._REQUIREMENTS
            if getattr(policy, f"{requirement_definition[0]}_required")
        ]
        if not requirements:
            return DecisionOutcome(0, 0, 0, ())

        consecutive_failures = {description: 0 for _, _, description in requirements}
        peak_failure_count = {description: 0 for _, _, description in requirements}
        highest_confidence = {description: 0.0 for _, _, description in requirements}
        latest_compliant = 0
        latest_unknown = 0

        for frame in detections.frames:
            people = [item for item in frame.objects if item.label == "person" and self._accepted(item, policy)]
            grid = self._evaluate_frame(people, frame.objects, requirements, policy)
            person_states = self._combine_person_states(grid, len(people), requirements)
            latest_compliant = sum(1 for state in person_states if state.compliant is True)
            latest_unknown = sum(1 for state in person_states if state.compliant is None)

            for _, _, description in requirements:
                statuses = grid[description]
                violations = [result for result in statuses if result.status is RequirementStatus.VIOLATION]
                compliant_count = sum(1 for result in statuses if result.status is RequirementStatus.COMPLIANT)
                if violations:
                    consecutive_failures[description] += 1
                    peak_failure_count[description] = max(peak_failure_count[description], len(violations))
                    highest_confidence[description] = max(
                        highest_confidence[description], max(result.confidence for result in violations)
                    )
                elif compliant_count > 0:
                    consecutive_failures[description] = 0
                # An UNKNOWN-only frame for this requirement holds the counter unchanged.

        requirement_decisions = tuple(
            RequirementDecision(
                requirement=description,
                persistent=consecutive_failures[description] >= policy.persistence_frames,
                non_compliant_count=peak_failure_count[description],
                confidence=highest_confidence[description] if peak_failure_count[description] else None,
            )
            for _, _, description in requirements
        )
        return DecisionOutcome(
            compliant_count=latest_compliant,
            non_compliant_count=sum(
                decision.non_compliant_count for decision in requirement_decisions if decision.persistent
            ),
            unknown_count=latest_unknown,
            requirement_decisions=requirement_decisions,
        )

    def person_states_for_frame(
        self, objects: tuple[DetectedObject, ...], policy: ZonePolicy
    ) -> tuple[PersonState, ...]:
        """Return one frame-scoped compliance state per detected person, in detector order.

        The returned sequence carries no identity: its order reflects only the detector's
        per-frame output and must never be used to correlate a person across frames or jobs.
        """
        people = [item for item in objects if item.label == "person" and self._accepted(item, policy)]
        requirements = [
            requirement
            for requirement in self._REQUIREMENTS
            if getattr(policy, f"{requirement[0]}_required")
        ]
        if not requirements:
            return tuple(PersonState(True, None, 0.0) for _ in people)

        grid = self._evaluate_frame(people, objects, requirements, policy)
        return self._combine_person_states(grid, len(people), requirements)

    def summarize_frame(self, objects: tuple[DetectedObject, ...], policy: ZonePolicy) -> tuple[int, int, int, int, tuple[float, ...]]:
        """Return a frame-scoped aggregate safety result without an identity or tracking key."""
        states = self.person_states_for_frame(objects, policy)
        compliant = sum(1 for state in states if state.compliant is True)
        non_compliant = sum(1 for state in states if state.compliant is False)
        unknown = sum(1 for state in states if state.compliant is None)
        confidences = tuple(state.confidence for state in states if state.compliant is False)
        return len(states), compliant, non_compliant, unknown, confidences

    def _evaluate_frame(
        self,
        people: list[DetectedObject],
        objects: tuple[DetectedObject, ...],
        requirements: list[tuple[str, str, str]],
        policy: ZonePolicy,
    ) -> dict[str, tuple[_PersonRequirementResult, ...]]:
        """Associate PPE to people for every active rule in one frame (FR-RULE-01..05).

        Positive and negative evidence are matched independently so FR-RULE-04's explicit
        negative precedence can be applied per person afterward; within each, every
        candidate is scored by containment and greedily assigned so one detected box can
        satisfy at most one person and each person gets only its single strongest match.
        """
        results: dict[str, list[_PersonRequirementResult]] = {description: [] for _, _, description in requirements}

        for positive_label, negative_label, description in requirements:
            region = self._REGIONS.get(positive_label, self._DEFAULT_REGION)
            positive_candidates = [item for item in objects if item.label == positive_label and self._accepted(item, policy)]
            negative_candidates = [item for item in objects if item.label == negative_label and self._accepted(item, policy)]
            positive_matches = self._best_exclusive_matches(people, positive_candidates, region)
            negative_matches = self._best_exclusive_matches(people, negative_candidates, region)

            for person_index in range(len(people)):
                if person_index in negative_matches:
                    item = negative_matches[person_index]
                    results[description].append(_PersonRequirementResult(person_index, RequirementStatus.VIOLATION, item.confidence))
                elif person_index in positive_matches:
                    results[description].append(_PersonRequirementResult(person_index, RequirementStatus.COMPLIANT, 0.0))
                else:
                    results[description].append(_PersonRequirementResult(person_index, RequirementStatus.UNKNOWN, 0.0))

        return {description: tuple(values) for description, values in results.items()}

    @staticmethod
    def _combine_person_states(
        grid: dict[str, tuple[_PersonRequirementResult, ...]],
        people_count: int,
        requirements: list[tuple[str, str, str]],
    ) -> tuple[PersonState, ...]:
        """Combine each person's per-requirement results into one overall state (worst wins)."""
        states = []
        for person_index in range(people_count):
            failed_requirement: str | None = None
            confidence = 0.0
            any_unknown = False
            for _, _, description in requirements:
                result = grid[description][person_index]
                if result.status is RequirementStatus.VIOLATION:
                    if failed_requirement is None:
                        failed_requirement = description
                        confidence = result.confidence
                elif result.status is RequirementStatus.UNKNOWN:
                    any_unknown = True
            if failed_requirement is not None:
                states.append(PersonState(False, failed_requirement, confidence))
            elif any_unknown:
                states.append(PersonState(None, None, 0.0))
            else:
                states.append(PersonState(True, None, 0.0))
        return tuple(states)

    @classmethod
    def _best_exclusive_matches(
        cls,
        people: list[DetectedObject],
        candidates: list[DetectedObject],
        region: tuple[float, float],
    ) -> dict[int, DetectedObject]:
        """Greedily assign each candidate box to at most one person's single strongest match.

        Every (person, candidate) pair eligible under the region/containment/size tests is
        scored, then claimed strongest-first: once a person has a match or a specific
        detected box has been claimed, neither is reconsidered. This is a greedy
        approximation, not a globally optimal assignment, which is an intentional,
        documented simplification -- adequate for the small number of people and PPE
        detections in one sampled frame.
        """
        scored: list[tuple[float, int, DetectedObject]] = []
        for person_index, person in enumerate(people):
            for item in candidates:
                score = cls._containment_score(person, item, region)
                if score is not None:
                    scored.append((score, person_index, item))
        scored.sort(key=lambda entry: entry[0], reverse=True)

        matched: dict[int, DetectedObject] = {}
        claimed_items: set[int] = set()
        for _, person_index, item in scored:
            if person_index in matched or id(item) in claimed_items:
                continue
            matched[person_index] = item
            claimed_items.add(id(item))
        return matched

    @classmethod
    def _containment_score(
        cls, person: DetectedObject, item: DetectedObject, region: tuple[float, float]
    ) -> float | None:
        """Return the fraction of ``item``'s box inside the person's rule-specific region.

        Returns None (ineligible) when the candidate is too small relative to the person to
        trust as real PPE evidence (FR-RULE-05), or has no meaningful overlap with the
        region at all.
        """
        person_area = person.box.width * person.box.height
        ppe_area = item.box.width * item.box.height
        if person_area <= 0 or ppe_area <= 0:
            return None
        if ppe_area < person_area * cls._MIN_RELATIVE_PPE_AREA:
            return None

        top_fraction, bottom_fraction = region
        region_box = BoundingBox(
            left=person.box.left - person.box.width * cls._HORIZONTAL_PADDING_RATIO,
            right=person.box.right + person.box.width * cls._HORIZONTAL_PADDING_RATIO,
            top=person.box.top + person.box.height * top_fraction,
            bottom=person.box.top + person.box.height * bottom_fraction,
        )
        intersection = cls._intersection_area(item.box, region_box)
        if intersection <= 0:
            return None
        containment = intersection / ppe_area
        if containment < cls._MIN_CONTAINMENT_RATIO:
            return None
        return containment

    @staticmethod
    def _intersection_area(a: BoundingBox, b: BoundingBox) -> float:
        """Return the overlap area of two boxes, or 0.0 when they do not overlap."""
        left = max(a.left, b.left)
        top = max(a.top, b.top)
        right = min(a.right, b.right)
        bottom = min(a.bottom, b.bottom)
        return max(0.0, right - left) * max(0.0, bottom - top)

    @staticmethod
    def _accepted(item: DetectedObject, policy: ZonePolicy) -> bool:
        """Reject detector evidence below the effective per-class or policy confidence threshold.

        ``class_confidence_thresholds`` (FR-DET-05), when present on the supplied policy view,
        overrides the flat ``confidence_threshold`` for one normalized label. It is looked up
        with a plain ``getattr`` so a bare ``ZonePolicy`` row or a test fixture without the
        attribute still falls back to the flat threshold unchanged.
        """
        overrides: dict[str, float] = getattr(policy, "class_confidence_thresholds", None) or {}
        threshold = overrides.get(item.label, policy.confidence_threshold)
        return item.confidence >= threshold
