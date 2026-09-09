"""Administrator-configurable runtime settings overriding fixed environment defaults."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.models import PlatformSettings

SINGLETON_ID = "default"


def get_platform_settings(session: Session) -> PlatformSettings:
    """Return the singleton settings row, seeding it from environment defaults if absent.

    The startup migration normally seeds this row once; the fallback here only matters for
    a session that has not run migrations (for example, an isolated unit test).
    """
    row = session.get(PlatformSettings, SINGLETON_ID)
    if row is not None:
        return row

    row = PlatformSettings(
        id=SINGLETON_ID,
        raw_media_retention_hours=settings.raw_media_retention_hours,
        frame_observation_retention_hours=settings.frame_observation_retention_hours,
        detection_provider=settings.detection_provider,
        demo_mode=settings.demo_mode,
        hf_model_repository=settings.hf_model_repository,
        hf_model_filename=settings.hf_model_filename,
        local_model_path=settings.local_model_path,
        detection_confidence_threshold=settings.detection_confidence_threshold,
    )
    session.add(row)
    session.flush()
    return row
