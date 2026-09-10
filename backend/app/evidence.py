"""Fail-closed face blurring and private evidence persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from app.config import settings
from app.detection import DetectedObject


class PrivacyProcessingError(RuntimeError):
    """Raised when mandatory face detection or blurring cannot complete safely."""


@dataclass(frozen=True)
class FaceDetectorReadiness:
    """Structured result of a face-detector model load-and-run verification.

    ``status`` is one of ``"ready"`` (model loaded and produced an inference result),
    ``"not_configured"`` (no model paths have been set yet -- expected in local
    development before Phase 7 evidence is enabled), or ``"unavailable"`` (paths are set
    but the files are missing, unreadable, or the model failed to load or run).
    """

    status: str
    detail: str


# PUBLIC_INTERFACE
def check_face_detector_readiness() -> FaceDetectorReadiness:
    """Verify the configured OpenCV DNN face-detector model loads and runs inference.

    This implements Phase 1's "Day 1" requirement: confirm the approved face model
    (``deploy.prototxt`` + ``res10_300x300_ssd_iter_140000_fp16.caffemodel``) loads and
    runs in the application/worker environment, even though the full blur pipeline is not
    wired into evidence review until Phase 7. Unlike ``_load_face_detector``, this never
    raises: it returns a structured result so application startup logging and a health
    endpoint can report readiness without crashing the process when the model is not yet
    configured (the expected state in local development).

    Returns:
        A ``FaceDetectorReadiness`` describing whether the configured model is ready,
        not yet configured, or configured but unavailable/failing.
    """
    if not settings.face_detector_prototxt_path or not settings.face_detector_model_path:
        return FaceDetectorReadiness(
            status="not_configured",
            detail="Face-detector model paths are not configured; evidence generation will fail closed.",
        )

    prototxt = Path(settings.face_detector_prototxt_path)
    model = Path(settings.face_detector_model_path)
    if not prototxt.is_file() or not model.is_file():
        return FaceDetectorReadiness(
            status="unavailable",
            detail="Configured face-detector model files are missing or unreadable.",
        )

    try:
        detector = cv2.dnn.readNetFromCaffe(str(prototxt), str(model))
        # A neutral, non-sensitive synthetic probe image -- this verifies the model can
        # load and execute a real forward pass without depending on any private media.
        probe = np.zeros((300, 300, 3), dtype=np.uint8)
        blob = cv2.dnn.blobFromImage(probe, 1.0, (300, 300), (104.0, 177.0, 123.0))
        detector.setInput(blob)
        result = detector.forward()
    except cv2.error as error:
        return FaceDetectorReadiness(
            status="unavailable",
            detail=f"Configured face-detector model failed to load or run: {error}",
        )

    if not isinstance(result, np.ndarray) or result.ndim != 4 or result.shape[3] < 7:
        return FaceDetectorReadiness(
            status="unavailable",
            detail="Configured face-detector model loaded but produced an invalid inference result.",
        )
    return FaceDetectorReadiness(status="ready", detail="Face-detector model loaded and ran successfully.")


class EvidenceService:
    """Create private evidence only after every detected face has been blurred."""

    def __init__(self) -> None:
        """Initialise private evidence storage without exposing its filesystem path."""
        self._root = Path(settings.private_evidence_directory)
        self._root.mkdir(parents=True, exist_ok=True)

    def create_annotated_blurred_evidence(
        self,
        media_path: str,
        objects: tuple[DetectedObject, ...],
        policy_result: str,
    ) -> str:
        """Annotate relevant safety detections, blur all faces, and store a protected JPEG.

        Raises:
            PrivacyProcessingError: If the source cannot be read, detector assets are unavailable,
                face detection fails, or the protected image cannot be saved.
        """
        frame = self._resize_for_evidence(self._read_first_frame(media_path))
        detector = self._load_face_detector()
        blurred = self._blur_detected_faces(frame, detector)
        annotated = self._annotate_safety_result(blurred, objects, policy_result)
        storage_key = f"{uuid4()}.jpg"

        if not cv2.imwrite(str(self._root / storage_key), annotated):
            raise PrivacyProcessingError("The privacy-processed evidence image could not be saved.")
        return storage_key

    def read(self, storage_key: str) -> bytes:
        """Read an opaque evidence object from private storage for an authorized API response."""
        path = self._root / storage_key
        if not path.is_file():
            raise FileNotFoundError("The evidence object is unavailable.")
        return path.read_bytes()

    def delete(self, storage_key: str) -> None:
        """Delete private evidence without leaking whether it had already been removed."""
        (self._root / storage_key).unlink(missing_ok=True)

    @staticmethod
    def _read_first_frame(media_path: str) -> np.ndarray:
        """Load a representative image frame from an approved private media file."""
        image = cv2.imread(media_path)
        if image is not None:
            return image

        capture = cv2.VideoCapture(media_path)
        try:
            success, frame = capture.read()
        finally:
            capture.release()
        if not success or frame is None:
            raise PrivacyProcessingError("No readable evidence frame was available from the submitted media.")
        return frame

    @staticmethod
    def _resize_for_evidence(image: np.ndarray) -> np.ndarray:
        """Bound stored evidence dimensions while preserving the original aspect ratio."""
        height, width = image.shape[:2]
        max_width = settings.max_evidence_image_width
        max_height = settings.max_evidence_image_height
        if max_width < 1 or max_height < 1:
            raise PrivacyProcessingError("Evidence image dimension limits must be positive.")
        scale = min(max_width / width, max_height / height, 1.0)
        if scale == 1.0:
            return image
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        return cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _annotate_safety_result(
        image: np.ndarray,
        objects: tuple[DetectedObject, ...],
        policy_result: str,
    ) -> np.ndarray:
        """Draw only relevant PPE and person boxes before mandatory anonymisation.

        Detection objects are frame-local and are never written to the database. The
        resulting image is immediately passed to the fail-closed face-blur stage.
        """
        output = image.copy()
        height, width = output.shape[:2]
        colours = {
            "person": (255, 153, 0),
            "helmet": (40, 170, 70),
            "no_helmet": (40, 40, 220),
            "vest": (40, 170, 70),
            "no_vest": (40, 40, 220),
        }
        for item in objects:
            if item.label not in colours:
                continue
            x1, y1, x2, y2 = EvidenceService._pixel_box(item, width, height)
            if x2 <= x1 or y2 <= y1:
                continue
            colour = colours[item.label]
            cv2.rectangle(output, (x1, y1), (x2, y2), colour, 2)
            cv2.putText(
                output,
                f"{item.label.replace('_', ' ')} {item.confidence:.0%}",
                (x1, max(16, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                colour,
                1,
                cv2.LINE_AA,
            )
        cv2.putText(
            output,
            f"Safety observation: {policy_result}",
            (12, max(24, height - 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            output,
            f"Safety observation: {policy_result}",
            (12, max(24, height - 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
        return output

    @staticmethod
    def _pixel_box(item: DetectedObject, width: int, height: int) -> tuple[int, int, int, int]:
        """Convert either normalized or pixel inference coordinates into image bounds."""
        box = item.box
        normalized = 0 <= box.left <= 1 and 0 <= box.top <= 1 and 0 <= box.right <= 1 and 0 <= box.bottom <= 1
        scale_x = width if normalized else 1
        scale_y = height if normalized else 1
        return (
            max(0, min(width, int(box.left * scale_x))),
            max(0, min(height, int(box.top * scale_y))),
            max(0, min(width, int(box.right * scale_x))),
            max(0, min(height, int(box.bottom * scale_y))),
        )

    @staticmethod
    def _load_face_detector() -> cv2.dnn_Net:
        """Load the configured ResNet SSD face-detector assets or fail closed."""
        prototxt = Path(settings.face_detector_prototxt_path)
        model = Path(settings.face_detector_model_path)
        if not prototxt.is_file() or not model.is_file():
            raise PrivacyProcessingError("Face-blur model assets are not configured.")
        try:
            return cv2.dnn.readNetFromCaffe(str(prototxt), str(model))
        except cv2.error as error:
            raise PrivacyProcessingError("Face-blur model assets could not be loaded.") from error

    @staticmethod
    def _blur_detected_faces(image: np.ndarray, detector: cv2.dnn_Net) -> np.ndarray:
        """Detect every face in one image and blur each safely bounded region.

        Any malformed detector result is treated as a privacy-gate failure rather than
        risking an unblurred image being stored or shown to a reviewer.
        """
        height, width = image.shape[:2]
        blob = cv2.dnn.blobFromImage(cv2.resize(image, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
        try:
            detector.setInput(blob)
            detections = detector.forward()
        except cv2.error as error:
            raise PrivacyProcessingError("Face detection failed during evidence processing.") from error

        if not isinstance(detections, np.ndarray) or detections.ndim != 4 or detections.shape[0:2] != (1, 1) or detections.shape[3] < 7:
            raise PrivacyProcessingError("Face detection returned an invalid result.")

        output = image.copy()
        kernel_size = settings.face_blur_kernel_size
        if kernel_size < 3:
            kernel_size = 3
        if kernel_size % 2 == 0:
            kernel_size += 1

        for detection in detections[0, 0, :, 2:7]:
            confidence, left, top, right, bottom = detection
            if not np.isfinite(detection).all():
                raise PrivacyProcessingError("Face detection returned an invalid result.")
            if float(confidence) < settings.face_detector_confidence:
                continue
            pad_x = int((right - left) * width * settings.face_blur_padding_ratio)
            pad_y = int((bottom - top) * height * settings.face_blur_padding_ratio)
            x1 = max(0, int(left * width) - pad_x)
            y1 = max(0, int(top * height) - pad_y)
            x2 = min(width, int(right * width) + pad_x)
            y2 = min(height, int(bottom * height) + pad_y)
            if x2 <= x1 or y2 <= y1:
                raise PrivacyProcessingError("Face detection returned an invalid bounding box.")
            output[y1:y2, x1:x2] = cv2.GaussianBlur(output[y1:y2, x1:x2], (kernel_size, kernel_size), 0)
        return output
