"""Tests for privacy-safe evidence annotation helpers."""

import numpy as np

from app.detection import BoundingBox, DetectedObject
from app.evidence import EvidenceService


def test_annotation_draws_relevant_normalized_detection() -> None:
    """Relevant normalized person/PPE boxes are rendered before face blurring."""
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    objects = (
        DetectedObject("person", 0.9, BoundingBox(0.1, 0.1, 0.8, 0.9)),
        DetectedObject("no_helmet", 0.8, BoundingBox(0.3, 0.1, 0.6, 0.3)),
    )

    annotated = EvidenceService._annotate_safety_result(image, objects, "helmet required")

    assert annotated.shape == image.shape
    assert np.any(annotated != image)


def test_annotation_ignores_unsupported_labels() -> None:
    """Unsupported labels do not create reviewer-facing evidence annotations."""
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    objects = (DetectedObject("unknown_label", 0.9, BoundingBox(0.1, 0.1, 0.8, 0.9)),)

    annotated = EvidenceService._annotate_safety_result(image, objects, "unknown")

    # The required policy-result banner remains, while unsupported object boxes are omitted.
    assert np.any(annotated[80:, :, :] != image[80:, :, :])
    assert np.array_equal(annotated[:70, :, :], image[:70, :, :])
