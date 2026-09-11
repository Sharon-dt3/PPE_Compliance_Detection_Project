"""Administrator-configurable runtime settings overriding fixed environment defaults."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import PlatformSettings

SINGLETON_ID = "default"
UNSPECIFIED_SHIFT = "unspecified"


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
        shift_schedule_json=settings.shift_schedule_json,
    )
    session.add(row)
    session.flush()
    return row


def resolve_shift(moment: datetime, schedule_json: str) -> str:
    """Return the configured shift name whose UTC hour window contains ``moment``.

    ``schedule_json`` maps a shift name to a ``[start_hour, end_hour)`` pair in UTC on a
    24-hour clock; a window may wrap past midnight (e.g. ``[18, 6]`` for a night shift).
    Returns ``"unspecified"`` -- the schema's own default -- when the schedule is empty,
    malformed, or no configured window matches, so an administrator-supplied schedule can
    never fail a media job; it just leaves that rollup unscoped by shift, same as before
    this function existed (FR-RPT-01's shift-scoped reporting was wired end to end at the
    read layer, but nothing ever computed a real value until now).

    ``moment`` is treated as UTC if it is naive, matching every other naive-datetime
    comparison in this codebase (see ``app/main.py``'s ``_as_utc``): SQLite's ``DateTime``
    round-trip drops tzinfo from a value that was always logically UTC when written, and
    ``datetime.astimezone()`` would otherwise silently reinterpret it as local system time.
    """
    try:
        schedule = json.loads(schedule_json)
    except (TypeError, ValueError):
        return UNSPECIFIED_SHIFT
    if not isinstance(schedule, dict):
        return UNSPECIFIED_SHIFT

    aware_moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    hour = aware_moment.astimezone(UTC).hour
    for name, window in schedule.items():
        if not (isinstance(window, list) and len(window) == 2):
            continue
        start, end = window
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < 24 and 0 <= end < 24):
            continue
        in_window = start <= hour < end if start <= end else (hour >= start or hour < end)
        if in_window:
            return str(name)
    return UNSPECIFIED_SHIFT
