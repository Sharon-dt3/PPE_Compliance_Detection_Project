"""Tests for label normalization and video sampling-rate resolution in the real provider.

Regression coverage for a bug found during review: UltralyticsPpeProvider dropped any raw
detector label with no entry in _LABELS via `continue`, even though the class carried an
_UNKNOWN_LABEL constant that was never actually used -- unmapped labels were silently lost
rather than preserved as "unknown_label" (FR-DET-04).
"""

from __future__ import annotations

import numpy as np

from app.detection import UltralyticsPpeProvider


class _FakeBox:
    """Minimal stand-in for one ultralytics detection box."""

    def __init__(self, cls_id: int, confidence: float, xyxy: np.ndarray) -> None:
        self.cls = [cls_id]
        self.conf = [confidence]
        self.xyxy = [xyxy]


class _FakeResult:
    """Minimal stand-in for one ultralytics per-frame prediction result."""

    def __init__(self, names: dict[int, str], boxes: list[_FakeBox]) -> None:
        self.names = names
        self.boxes = boxes


class _FakeModel:
    """Minimal stand-in for a loaded ultralytics YOLO model."""

    def __init__(self, results: list[_FakeResult]) -> None:
        self._results = results

    def predict(self, **_kwargs: object) -> list[_FakeResult]:
        """Return the fixed fake results regardless of the requested prediction options."""
        return self._results


def _provider() -> UltralyticsPpeProvider:
    return UltralyticsPpeProvider(
        hf_model_repository="unused/repo", hf_model_filename="best.pt", local_model_path="", confidence_threshold=0.25
    )


def test_evaluate_preserves_unmapped_label_as_unknown_label(monkeypatch) -> None:
    """An unmapped raw detector label becomes unknown_label instead of being dropped."""
    names = {0: "helmet", 1: "mystery-object"}
    boxes = [
        _FakeBox(0, 0.9, np.array([0.0, 0.0, 10.0, 10.0])),
        _FakeBox(1, 0.8, np.array([5.0, 5.0, 15.0, 15.0])),
    ]
    provider = _provider()
    monkeypatch.setattr(provider, "_load_model", lambda: _FakeModel([_FakeResult(names, boxes)]))

    outcome = provider.evaluate("unused-media-path.jpg")

    labels = [item.label for item in outcome.frames[0].objects]
    assert labels == ["helmet", "unknown_label"]


def test_evaluate_still_normalizes_known_labels(monkeypatch) -> None:
    """Known raw labels keep normalizing correctly alongside an unmapped one in the same frame."""
    names = {0: "No-Hardhat", 1: "Safety Vest", 2: "unrelated-class"}
    boxes = [
        _FakeBox(0, 0.9, np.array([0.0, 0.0, 10.0, 10.0])),
        _FakeBox(1, 0.85, np.array([1.0, 1.0, 5.0, 5.0])),
        _FakeBox(2, 0.4, np.array([2.0, 2.0, 3.0, 3.0])),
    ]
    provider = _provider()
    monkeypatch.setattr(provider, "_load_model", lambda: _FakeModel([_FakeResult(names, boxes)]))

    outcome = provider.evaluate("unused-media-path.jpg")

    labels = {item.label for item in outcome.frames[0].objects}
    assert labels == {"no_helmet", "vest", "unknown_label"}


def test_resolve_vid_stride_computes_stride_from_native_fps(tmp_path) -> None:
    """A target sampling rate is translated into a stride using the video's real native FPS."""
    cv2 = __import__("cv2")
    video_path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (16, 16))
    try:
        for _ in range(5):
            writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    finally:
        writer.release()

    stride = UltralyticsPpeProvider._resolve_vid_stride(str(video_path), 3.0)

    assert stride == 10


def test_resolve_vid_stride_defaults_to_one_without_a_target() -> None:
    """No configured sampling rate means every frame is processed (native provider behavior)."""
    assert UltralyticsPpeProvider._resolve_vid_stride("irrelevant.mp4", None) == 1


def test_resolve_vid_stride_defaults_to_one_for_unreadable_media() -> None:
    """An unreadable path fails safe to stride 1 rather than raising over a sampling preference."""
    assert UltralyticsPpeProvider._resolve_vid_stride("/nonexistent/path.mp4", 3.0) == 1
