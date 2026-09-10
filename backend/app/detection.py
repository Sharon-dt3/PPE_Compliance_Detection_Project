"""Detection-provider boundary and geometry-preserving YOLO PPE inference."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session


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

    def evaluate(self, media_path: str, *, sampling_fps: float | None = None) -> DetectionOutcome:
        """Evaluate private media and return frame-level detector observations.

        ``sampling_fps``, when given, is the zone policy's configured target detector
        sampling rate for a video source (FR-ING/Blueprint "video sampling rate"); it has no
        meaningful effect on a still image and a provider may ignore it if inapplicable.
        """


class DemoDetectionProvider:
    """Deterministic provider for explicit workflow testing only."""

    def evaluate(self, media_path: str, *, sampling_fps: float | None = None) -> DetectionOutcome:
        """Return three persistent explicit helmet failures for workflow testing.

        ``sampling_fps`` is accepted for interface compatibility and has no effect: this
        provider's output is fixed and never reads the media file.
        """
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

    # Normalizes every supported provider's raw class names to the application's shared
    # label set (FR-DET-02). A raw label with no entry here becomes "unknown_label" rather
    # than being silently dropped (FR-DET-04): it stays visible for benchmark/evaluation
    # review, while the compliance rules in decision.py simply never look for that label.
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
        "safety-vest": "vest",
        "no-vest": "no_vest",
        "no vest": "no_vest",
        "no-safety-vest": "no_vest",
        "glove": "gloves",
        "gloves": "gloves",
        "glass": "glasses",
        "glasses": "glasses",
        "goggle": "glasses",
        "goggles": "glasses",
    }
    _UNKNOWN_LABEL = "unknown_label"

    def __init__(
        self,
        *,
        hf_model_repository: str,
        hf_model_filename: str,
        local_model_path: str,
        confidence_threshold: float,
    ) -> None:
        """Capture the resolved, administrator-configured model selection for this run."""
        self._hf_model_repository = hf_model_repository
        self._hf_model_filename = hf_model_filename
        self._local_model_path = local_model_path
        self._confidence_threshold = confidence_threshold

    def evaluate(self, media_path: str, *, sampling_fps: float | None = None) -> DetectionOutcome:
        """Run YOLO and return labeled boxes without assigning any persistent identity.

        ``sampling_fps`` (the active zone policy's configured target rate) is translated
        into an Ultralytics video frame stride; it has no effect on a still image.
        """
        model = self._load_model()
        results = model.predict(
            source=media_path,
            conf=self._confidence_threshold,
            verbose=False,
            stream=False,
            vid_stride=self._resolve_vid_stride(media_path, sampling_fps),
        )
        frames: list[FrameDetections] = []

        for frame_index, result in enumerate(results):
            objects: list[DetectedObject] = []
            names = result.names
            for box in result.boxes:
                label = self._LABELS.get(str(names[int(box.cls[0])]).lower(), self._UNKNOWN_LABEL)
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

    def _model_location(self) -> str:
        """Resolve the selected local or Hugging Face model identifier."""
        return self._local_model_path or f"{self._hf_model_repository}/{self._hf_model_filename}"

    def _load_model(self):
        """Load the approved configured model without silently substituting a fallback."""
        try:
            from huggingface_hub import hf_hub_download
            from ultralytics import YOLO
        except ImportError as error:
            raise RuntimeError("The real inference dependencies are not installed.") from error

        if self._local_model_path:
            model_path = Path(self._local_model_path)
            if not model_path.is_file():
                raise RuntimeError("The configured local PPE model file is unavailable.")
        else:
            model_path = Path(
                hf_hub_download(repo_id=self._hf_model_repository, filename=self._hf_model_filename)
            )
        return YOLO(str(model_path))

    @staticmethod
    def _resolve_vid_stride(media_path: str, sampling_fps: float | None) -> int:
        """Translate a target sampling rate into an Ultralytics video frame stride.

        Falls back to processing every frame (stride 1) when no target is configured, the
        source's native frame rate cannot be read (missing file, unsupported codec, or a
        still image), or the OpenCV dependency is unavailable -- a sampling-rate preference
        must never fail a job outright.
        """
        if not sampling_fps or sampling_fps <= 0:
            return 1
        try:
            import cv2
        except ImportError:
            return 1

        capture = cv2.VideoCapture(media_path)
        try:
            native_fps = capture.get(cv2.CAP_PROP_FPS)
        finally:
            capture.release()
        if not native_fps or native_fps <= 0:
            return 1
        return max(1, round(native_fps / sampling_fps))


def get_detection_provider(session: Session) -> DetectionProvider:
    """Return only the configured inference provider; unknown choices fail closed.

    Configuration is read from the administrator-editable ``PlatformSettings`` row rather
    than the environment directly, so a change made through ``/api/v1/settings/inference``
    takes effect on the next job without a redeploy.
    """
    from app.platform_settings import get_platform_settings

    config = get_platform_settings(session)
    if config.demo_mode and config.detection_provider == "demo":
        return DemoDetectionProvider()
    if not config.demo_mode and config.detection_provider == "ultralytics":
        return UltralyticsPpeProvider(
            hf_model_repository=config.hf_model_repository,
            hf_model_filename=config.hf_model_filename,
            local_model_path=config.local_model_path,
            confidence_threshold=config.detection_confidence_threshold,
        )
    raise RuntimeError("No approved PPE inference provider is configured.")
