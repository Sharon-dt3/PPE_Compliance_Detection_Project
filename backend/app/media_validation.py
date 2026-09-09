"""Safe validation of manual POC media uploads before private persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastapi import HTTPException, status

from app.config import settings

_ALLOWED_MEDIA = {
    ".jpg": ("image/jpeg", "jpeg"),
    ".jpeg": ("image/jpeg", "jpeg"),
    ".png": ("image/png", "png"),
    ".mp4": ("video/mp4", "mp4"),
    ".mov": ("video/quicktime", "mov"),
}


@dataclass(frozen=True)
class ValidatedMedia:
    """Validated upload metadata used when creating a private processing job."""

    content_type: str
    media_kind: str


def validate_media_upload(filename: str, content_type: str | None, content: bytes) -> ValidatedMedia:
    """Validate media filename, declared type, signature, dimensions, and duration."""
    suffix = Path(filename).suffix.lower()
    expected = _ALLOWED_MEDIA.get(suffix)
    if expected is None:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Supported uploads are JPEG, PNG, MP4, and MOV.")

    expected_type, media_kind = expected
    if content_type != expected_type:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="The file extension and declared media type do not match.")
    if media_kind in {"jpeg", "png"}:
        _validate_image(content, media_kind)
    else:
        _validate_video_signature(content, media_kind)
    return ValidatedMedia(content_type=expected_type, media_kind=media_kind)


def validate_video_file(path: str) -> None:
    """Validate video dimensions and duration after the private upload has been saved."""
    try:
        import cv2
    except ImportError as error:
        raise ValueError("Video validation is unavailable because its approved decoder is not installed.") from error

    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise ValueError("The video cannot be opened.")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()

    if width <= 0 or height <= 0 or width > settings.max_frame_width or height > settings.max_frame_height:
        raise ValueError("The video dimensions exceed the approved limit.")
    if fps <= 0 or frame_count < 0 or frame_count / fps > settings.max_video_duration_seconds:
        raise ValueError("The video duration exceeds the approved limit.")


def _validate_image(content: bytes, media_kind: str) -> None:
    """Confirm image signatures and configured frame dimensions."""
    is_jpeg = content.startswith(b"\xff\xd8\xff")
    is_png = content.startswith(b"\x89PNG\r\n\x1a\n")
    if (media_kind == "jpeg" and not is_jpeg) or (media_kind == "png" and not is_png):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file content does not match its declared image format.")

    try:
        import cv2
    except ImportError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image validation is unavailable because its approved decoder is not installed.",
        ) from error

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded image cannot be decoded.")
    height, width = image.shape[:2]
    if width > settings.max_frame_width or height > settings.max_frame_height:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The image dimensions exceed the approved limit.")


def _validate_video_signature(content: bytes, media_kind: str) -> None:
    """Confirm the ISO base-media signature used by supported MP4/MOV uploads."""
    if len(content) < 12 or content[4:8] != b"ftyp":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file content does not match a supported video format.")
    brand = content[8:12]
    if media_kind == "mp4" and brand not in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file content does not match an MP4 upload.")
