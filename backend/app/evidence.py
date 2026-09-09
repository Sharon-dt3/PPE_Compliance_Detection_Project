"""Fail-closed face blurring and private evidence persistence."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from app.config import settings


class PrivacyProcessingError(RuntimeError):
    """Raised when mandatory face detection or blurring cannot complete safely."""


class EvidenceService:
    """Create private evidence only after every detected face has been blurred."""

    def __init__(self) -> None:
        """Initialise private evidence storage without exposing its filesystem path."""
        self._root = Path(settings.private_evidence_directory)
        self._root.mkdir(parents=True, exist_ok=True)

    def create_blurred_evidence(self, media_path: str) -> str:
        """Blur all faces from the first readable video/image frame and store the processed JPEG.

        Raises:
            PrivacyProcessingError: If the source cannot be read, detector assets are unavailable,
                face detection fails, or the protected image cannot be saved.
        """
        frame = self._read_first_frame(media_path)
        detector = self._load_face_detector()
        blurred = self._blur_detected_faces(frame, detector)
        storage_key = f"{uuid4()}.jpg"

        if not cv2.imwrite(str(self._root / storage_key), blurred):
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
        """Detect every face in one image and blur each safely bounded region."""
        height, width = image.shape[:2]
        blob = cv2.dnn.blobFromImage(cv2.resize(image, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
        try:
            detector.setInput(blob)
            detections = detector.forward()
        except cv2.error as error:
            raise PrivacyProcessingError("Face detection failed during evidence processing.") from error

        output = image.copy()
        kernel_size = settings.face_blur_kernel_size
        if kernel_size < 3:
            kernel_size = 3
        if kernel_size % 2 == 0:
            kernel_size += 1

        for confidence, left, top, right, bottom in detections[0, 0, :, 2:7]:
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
