"""Detection-provider boundary and geometry-preserving YOLO PPE inference."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import settings


@dataclass(frozen=True)
class BoundingBox:
    """A normalized, non-identifying detection bounding box."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        """Return the non-negative box width."""
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        """Return the non-negative box height."""
        return max(0.0, self.bottom - self.top)


@dataclass(frozen=True)
class DetectedObject:
    """One detector result containing no identity, embedding, or tracking data."""

    label: str
    confidence: float
    box: BoundingBox


@dataclass(frozen=True)
class FrameDetections:
    """Detector output for one sampled frame."""

    frame_index: int
    objects: tuple[DetectedObject, ...]


@dataclass(frozen=True)
class DetectionOutcome:
    """Normalized non-identifying detector output for safety decision processing."""

    frames: tuple[FrameDetections, ...]
    provider_name: str
    provider_version: str


class DetectionProvider(Protocol):
    """Contract implemented by real and controlled-demo PPE inference providers."""

    def evaluate(self, media_path: str) -> DetectionOutcome:
        """Evaluate private media and return frame-level detector observations."""


class DemoDetectionProvider:
    """Deterministic provider for explicit workflow testing only."""

    def evaluate(self, media_path: str) -> DetectionOutcome:
        """Return three persistent explicit helmet failures for workflow testing."""
        person = BoundingBox(0.25, 0.15, 0.75, 0.95)
        no_helmet = BoundingBox(0.35, 0.16, 0.65, 0.38)
        frames = tuple(
            FrameDetections(
                frame_index=index,
                objects=(
                    DetectedObject("person", 0.96, person),
                    DetectedObject("no_helmet", 0.91, no_helmet),
                ),
            )
            for index in range(3)
        )
        return DetectionOutcome(frames=frames, provider_name="deterministic-demo", provider_version="0.2")


class UltralyticsPpeProvider:
    """Run an explicitly configured YOLO PPE model against private media."""

    _LABELS = {
        "human": "person",
        "person": "person",
        "helmet": "helmet",
        "hardhat": "helmet",
        "hard-hat": "helmet",
        "no-helmet": "no_helmet",
        "no helmet": "no_helmet",
        "no-hardhat": "no_helmet",
        "no hardhat": "no_helmet",
        "vest": "vest",
        "safety vest": "vest",
        "no-vest": "no_vest",
        "no vest": "no_vest",
    }

    def evaluate(self, media_path: str) -> DetectionOutcome:
        """Run YOLO and return labeled boxes without assigning any persistent identity."""
        model = self._load_model()
        results = model.predict(
            source=media_path,
            conf=settings.detection_confidence_threshold,
            verbose=False,
            stream=False,
        )
        frames: list[FrameDetections] = []

        for frame_index, result in enumerate(results):
            objects: list[DetectedObject] = []
            names = result.names
            for box in result.boxes:
                label = self._LABELS.get(str(names[int(box.cls[0])]).lower())
                if label is None:
                    continue
                coordinates = [float(value) for value in box.xyxy[0].tolist()]
                objects.append(
                    DetectedObject(
                        label=label,
                        confidence=float(box.conf[0]),
                        box=BoundingBox(*coordinates),
                    )
                )
            frames.append(FrameDetections(frame_index=frame_index, objects=tuple(objects)))

        return DetectionOutcome(
            frames=tuple(frames),
            provider_name="ultralytics-yolo",
            provider_version=self._model_location(),
        )

    @staticmethod
    def _model_location() -> str:
        """Resolve the selected local or Hugging Face model identifier."""
        return settings.local_model_path or f"{settings.hf_model_repository}/{settings.hf_model_filename}"

    @staticmethod
    def _load_model():
        """Load the approved configured model without silently substituting a fallback."""
        try:
            from huggingface_hub import hf_hub_download
            from ultralytics import YOLO
        except ImportError as error:
            raise RuntimeError("The real inference dependencies are not installed.") from error

        if settings.local_model_path:
            model_path = Path(settings.local_model_path)
            if not model_path.is_file():
                raise RuntimeError("The configured local PPE model file is unavailable.")
        else:
            model_path = Path(
                hf_hub_download(repo_id=settings.hf_model_repository, filename=settings.hf_model_filename)
            )
        return YOLO(str(model_path))


def get_detection_provider() -> DetectionProvider:
    """Return only the configured inference provider; unknown choices fail closed."""
    if settings.demo_mode and settings.detection_provider == "demo":
        return DemoDetectionProvider()
    if not settings.demo_mode and settings.detection_provider == "ultralytics":
        return UltralyticsPpeProvider()
    raise RuntimeError("No approved PPE inference provider is configured.")
