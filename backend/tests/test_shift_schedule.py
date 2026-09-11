"""Tests for the shift-schedule gap found auditing FR-RPT-01: `MetricRollup.shift` was
always the schema default ("unspecified") because nothing ever computed a real value, even
though shift-scoped filtering/reporting/export was fully wired up at the read layer. These
cover `resolve_shift()` directly plus its wiring into the real `process_media_job()` pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import SessionLocal, initialise_database
from app.models import CameraSource, JobStatus, MediaJob, MetricRollup, Zone, ZonePolicy
from app.platform_settings import get_platform_settings, resolve_shift
from app.processing import process_media_job

initialise_database()

DEFAULT_SCHEDULE = '{"day": [6, 18], "night": [18, 6]}'


def test_resolve_shift_matches_a_simple_day_window() -> None:
    moment = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    assert resolve_shift(moment, DEFAULT_SCHEDULE) == "day"


def test_resolve_shift_matches_a_wraparound_night_window() -> None:
    """A [18, 6] window must match both 22:00 and 03:00 -- either side of midnight."""
    assert resolve_shift(datetime(2026, 9, 12, 22, 0, tzinfo=UTC), DEFAULT_SCHEDULE) == "night"
    assert resolve_shift(datetime(2026, 9, 12, 3, 0, tzinfo=UTC), DEFAULT_SCHEDULE) == "night"


def test_resolve_shift_boundary_hours_are_inclusive_start_exclusive_end() -> None:
    assert resolve_shift(datetime(2026, 9, 12, 6, 0, tzinfo=UTC), DEFAULT_SCHEDULE) == "day"
    assert resolve_shift(datetime(2026, 9, 12, 17, 59, tzinfo=UTC), DEFAULT_SCHEDULE) == "day"
    assert resolve_shift(datetime(2026, 9, 12, 18, 0, tzinfo=UTC), DEFAULT_SCHEDULE) == "night"


def test_resolve_shift_converts_a_non_utc_moment_before_matching() -> None:
    """A tz-aware but non-UTC timestamp must be normalized, not compared by raw hour."""
    from datetime import timezone

    plus_five = timezone(timedelta(hours=5))
    # 03:00+05:00 is 22:00 UTC the previous day -- squarely inside the night window.
    moment = datetime(2026, 9, 12, 3, 0, tzinfo=plus_five)
    assert resolve_shift(moment, DEFAULT_SCHEDULE) == "night"


def test_resolve_shift_falls_back_to_unspecified_for_an_empty_schedule() -> None:
    assert resolve_shift(datetime(2026, 9, 12, 10, 0, tzinfo=UTC), "{}") == "unspecified"


def test_resolve_shift_falls_back_to_unspecified_for_malformed_json() -> None:
    """A corrupted admin-supplied schedule must never raise during job processing."""
    assert resolve_shift(datetime(2026, 9, 12, 10, 0, tzinfo=UTC), "not valid json") == "unspecified"
    assert resolve_shift(datetime(2026, 9, 12, 10, 0, tzinfo=UTC), "[]") == "unspecified"


def test_resolve_shift_skips_a_malformed_window_and_keeps_checking_others() -> None:
    schedule = json.dumps({"broken": [6], "day": [6, 18]})
    assert resolve_shift(datetime(2026, 9, 12, 10, 0, tzinfo=UTC), schedule) == "day"


def test_completed_job_rollup_gets_a_real_shift_not_the_schema_default() -> None:
    """End-to-end: a job processed right now must not leave MetricRollup.shift as 'unspecified'.

    Pins PlatformSettings.shift_schedule_json to the known default explicitly: it is a real
    singleton row in a shared, persistent test database (see conftest.py), so another test
    may have already changed it to something with gaps that could legitimately produce
    "unspecified" depending on wall-clock time -- this test needs a schedule that covers all
    24 hours to be deterministic regardless of run order or time of day.
    """
    with SessionLocal() as session:
        get_platform_settings(session).shift_schedule_json = DEFAULT_SCHEDULE
        session.commit()

    with SessionLocal() as session:
        suffix = uuid4().hex[:8]
        zone = Zone(name=f"shift-test-zone-{suffix}", description="test")
        session.add(zone)
        session.flush()
        policy = ZonePolicy(zone_id=zone.id, version=1, helmet_required=True, vest_required=False, persistence_frames=1)
        session.add(policy)
        source = CameraSource(name=f"shift-test-source-{suffix}", zone_id=zone.id)
        session.add(source)
        session.flush()
        job = MediaJob(
            source_id=source.id,
            zone_id=zone.id,
            policy_id=policy.id,
            filename="clip.jpg",
            content_type="image/jpeg",
            storage_key=f"unused-{suffix}",
            status=JobStatus.QUEUED.value,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    process_media_job(job_id)

    with SessionLocal() as session:
        rollup = session.query(MetricRollup).filter_by(job_id=job_id).one()
        completed_job = session.query(MediaJob).filter_by(id=job_id).one()
        # Compare against the job's own completed_at (not a fresh datetime.now(UTC) call)
        # to avoid a sub-second race across an hour boundary in the default schedule.
        expected = resolve_shift(completed_job.completed_at, DEFAULT_SCHEDULE)
        assert rollup.shift == expected
        assert rollup.shift != "unspecified"
