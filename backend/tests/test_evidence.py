"""Tests for privacy-safe evidence annotation helpers."""

import numpy as np
import pytest

from app.detection import BoundingBox, DetectedObject
from app.config import settings
from app.evidence import EvidenceService, PrivacyProcessingError, check_face_detector_readiness


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


def test_invalid_face_detector_result_fails_closed() -> None:
    """Malformed DNN output blocks evidence instead of risking unblurred persistence."""

    class InvalidDetector:
        """Minimal invalid detector response fixture."""

        def setInput(self, _blob: np.ndarray) -> None:
            """Accept a detector blob for compatibility with the service."""

        def forward(self) -> np.ndarray:
            """Return an invalid result shape to simulate a detector fault."""
            return np.array([1.0])

    with pytest.raises(PrivacyProcessingError, match="invalid result"):
        EvidenceService._blur_detected_faces(np.zeros((100, 100, 3), dtype=np.uint8), InvalidDetector())


def test_evidence_dimensions_are_bounded_without_upscaling() -> None:
    """Evidence resizing preserves aspect ratio and does not enlarge small images."""
    image = np.zeros((2160, 3840, 3), dtype=np.uint8)

    bounded = EvidenceService._resize_for_evidence(image)
    unchanged = EvidenceService._resize_for_evidence(np.zeros((100, 100, 3), dtype=np.uint8))

    assert bounded.shape[:2] == (1080, 1920)
    assert unchanged.shape[:2] == (100, 100)


def test_face_detector_readiness_reports_not_configured_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no model paths set (the local-development default), readiness is not_configured."""
    monkeypatch.setattr(settings, "face_detector_prototxt_path", "")
    monkeypatch.setattr(settings, "face_detector_model_path", "")

    readiness = check_face_detector_readiness()

    assert readiness.status == "not_configured"


def test_face_detector_readiness_reports_unavailable_for_missing_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured paths that do not point to real files are reported as unavailable, not a crash."""
    monkeypatch.setattr(settings, "face_detector_prototxt_path", "/nonexistent/deploy.prototxt")
    monkeypatch.setattr(settings, "face_detector_model_path", "/nonexistent/model.caffemodel")

    readiness = check_face_detector_readiness()

    assert readiness.status == "unavailable"


def test_face_detector_readiness_reports_ready_when_model_loads_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A model that loads and produces a valid forward-pass result is reported ready."""
    import cv2

    prototxt_path = tmp_path / "deploy.prototxt"
    model_path = tmp_path / "model.caffemodel"
    prototxt_path.write_text("placeholder")
    model_path.write_bytes(b"placeholder")
    monkeypatch.setattr(settings, "face_detector_prototxt_path", str(prototxt_path))
    monkeypatch.setattr(settings, "face_detector_model_path", str(model_path))

    class _FakeDetector:
        """Minimal stand-in for a loaded OpenCV DNN network."""

        def setInput(self, _blob: np.ndarray) -> None:
            """Accept a detector blob for compatibility with the readiness check."""

        def forward(self) -> np.ndarray:
            """Return a well-formed, empty detection result."""
            return np.zeros((1, 1, 0, 7), dtype=np.float32)

    monkeypatch.setattr(cv2.dnn, "readNetFromCaffe", lambda *_args, **_kwargs: _FakeDetector())

    readiness = check_face_detector_readiness()

    assert readiness.status == "ready"


def test_face_detector_readiness_reports_unavailable_when_load_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A model file that exists but fails to load is reported unavailable, not raised."""
    import cv2

    prototxt_path = tmp_path / "deploy.prototxt"
    model_path = tmp_path / "model.caffemodel"
    prototxt_path.write_text("placeholder")
    model_path.write_bytes(b"placeholder")
    monkeypatch.setattr(settings, "face_detector_prototxt_path", str(prototxt_path))
    monkeypatch.setattr(settings, "face_detector_model_path", str(model_path))

    def _raise_cv2_error(*_args: object, **_kwargs: object) -> None:
        raise cv2.error("simulated load failure")

    monkeypatch.setattr(cv2.dnn, "readNetFromCaffe", _raise_cv2_error)

    readiness = check_face_detector_readiness()

    assert readiness.status == "unavailable"
