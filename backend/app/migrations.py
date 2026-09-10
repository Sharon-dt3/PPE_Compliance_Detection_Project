"""Small, dependency-free additive schema migrations for the PPE POC."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Connection, inspect, text

from app.config import settings

MIGRATION_TABLE = "schema_migrations"


def apply_migrations(connection: Connection) -> None:
    """Apply every outstanding additive migration in deterministic revision order."""
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} "
            "(revision VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    applied = {row[0] for row in connection.execute(text(f"SELECT revision FROM {MIGRATION_TABLE}")).all()}

    migrations: tuple[tuple[str, Callable[[Connection], None]], ...] = (
        ("20260909_auth_lifecycle_and_audit", _upgrade_auth_lifecycle_and_audit),
        ("20260909_reporting_and_model_evaluations", _upgrade_reporting_and_model_evaluations),
        ("20260909_frame_observations", _upgrade_frame_observations),
        ("20260909_frame_observation_expiry", _upgrade_frame_observation_expiry),
        ("20260909_person_observations", _upgrade_person_observations),
        ("20260909_platform_settings", _upgrade_platform_settings),
        ("20260909_evidence_demo_approved", _upgrade_evidence_demo_approved),
        ("20260909_class_and_source_thresholds", _upgrade_class_and_source_thresholds),
        ("20260910_sampling_fps", _upgrade_sampling_fps),
        ("20260910_event_rule_results", _upgrade_event_rule_results),
        ("20260910_event_acknowledgements", _upgrade_event_acknowledgements),
        ("20260910_policy_effective_window", _upgrade_policy_effective_window),
        ("20260910_test_media_retention", _upgrade_test_media_retention),
    )
    for revision, upgrade in migrations:
        if revision in applied:
            continue
        upgrade(connection)
        connection.execute(
            text(f"INSERT INTO {MIGRATION_TABLE} (revision) VALUES (:revision)"),
            {"revision": revision},
        )


def _upgrade_auth_lifecycle_and_audit(connection: Connection) -> None:
    """Add lifecycle and audit attribution fields to older local POC databases."""
    _add_missing_columns(
        connection,
        "compliance_alerts",
        {
            "deduplication_key": "VARCHAR(255)",
            "occurrence_count": "INTEGER DEFAULT 1 NOT NULL",
            "first_observed_at": "TIMESTAMP",
            "last_observed_at": "TIMESTAMP",
            "resolved_at": "TIMESTAMP",
            "resolution_note": "TEXT",
        },
    )
    _add_missing_columns(connection, "media_jobs", {"failure_code": "VARCHAR(80)"})
    _add_missing_columns(connection, "audit_events", {"actor_reference": "VARCHAR(128)"})

    connection.execute(
        text(
            "UPDATE compliance_alerts "
            "SET deduplication_key = source_id || ':' || policy_id || ':' || failed_requirement "
            "WHERE deduplication_key IS NULL"
        )
    )
    connection.execute(
        text(
            "UPDATE compliance_alerts "
            "SET first_observed_at = COALESCE(first_observed_at, created_at), "
            "last_observed_at = COALESCE(last_observed_at, created_at)"
        )
    )
    connection.execute(text("UPDATE audit_events SET actor_reference = 'legacy-event' WHERE actor_reference IS NULL"))


def _upgrade_reporting_and_model_evaluations(connection: Connection) -> None:
    """Add aggregate reporting dimensions when upgrading an existing database."""
    _add_missing_columns(
        connection,
        "metric_rollups",
        {
            "shift": "VARCHAR(50) DEFAULT 'unspecified' NOT NULL",
            "rule_key": "VARCHAR(120) DEFAULT 'all_required_ppe' NOT NULL",
        },
    )


def _upgrade_frame_observations(connection: Connection) -> None:
    """Create short-lived non-identifying sampled-frame summaries when absent."""
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS frame_observations ("
            "id VARCHAR(36) PRIMARY KEY, "
            "job_id VARCHAR(36) NOT NULL, "
            "frame_index INTEGER NOT NULL, "
            "person_count INTEGER NOT NULL, "
            "compliant_count INTEGER NOT NULL, "
            "non_compliant_count INTEGER NOT NULL, "
            "unknown_count INTEGER NOT NULL, "
            "confidence_summary TEXT NOT NULL, "
            "expires_at TIMESTAMP, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_frame_observations_job_id ON frame_observations (job_id)"))


def _upgrade_frame_observation_expiry(connection: Connection) -> None:
    """Add an explicit expiry timestamp to legacy frame summaries safely."""
    _add_missing_columns(connection, "frame_observations", {"expires_at": "TIMESTAMP"})
    connection.execute(
        text(
            "UPDATE frame_observations "
            "SET expires_at = datetime(created_at, '+24 hours') "
            "WHERE expires_at IS NULL"
        )
    )
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_frame_observations_expires_at ON frame_observations (expires_at)")
    )


def _upgrade_person_observations(connection: Connection) -> None:
    """Create short-lived, non-identifying per-person frame observations when absent."""
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS person_observations ("
            "id VARCHAR(36) PRIMARY KEY, "
            "job_id VARCHAR(36) NOT NULL, "
            "frame_index INTEGER NOT NULL, "
            "person_index INTEGER NOT NULL, "
            "state VARCHAR(20) NOT NULL, "
            "failed_requirement VARCHAR(120), "
            "confidence FLOAT, "
            "expires_at TIMESTAMP NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_person_observations_job_id ON person_observations (job_id)"))
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_person_observations_expires_at ON person_observations (expires_at)")
    )


def _upgrade_platform_settings(connection: Connection) -> None:
    """Create the singleton administrator-configurable retention/inference settings row."""
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS platform_settings ("
            "id VARCHAR(20) PRIMARY KEY, "
            "raw_media_retention_hours INTEGER NOT NULL, "
            "frame_observation_retention_hours INTEGER NOT NULL, "
            "detection_provider VARCHAR(20) NOT NULL, "
            "demo_mode BOOLEAN NOT NULL, "
            "hf_model_repository VARCHAR(200) NOT NULL, "
            "hf_model_filename VARCHAR(200) NOT NULL, "
            "local_model_path VARCHAR(500) NOT NULL DEFAULT '', "
            "detection_confidence_threshold FLOAT NOT NULL, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO platform_settings ("
            "id, raw_media_retention_hours, frame_observation_retention_hours, detection_provider, "
            "demo_mode, hf_model_repository, hf_model_filename, local_model_path, detection_confidence_threshold"
            ") SELECT 'default', :raw_media_retention_hours, :frame_observation_retention_hours, :detection_provider, "
            ":demo_mode, :hf_model_repository, :hf_model_filename, :local_model_path, :detection_confidence_threshold "
            "WHERE NOT EXISTS (SELECT 1 FROM platform_settings WHERE id = 'default')"
        ),
        {
            "raw_media_retention_hours": settings.raw_media_retention_hours,
            "frame_observation_retention_hours": settings.frame_observation_retention_hours,
            "detection_provider": settings.detection_provider,
            "demo_mode": settings.demo_mode,
            "hf_model_repository": settings.hf_model_repository,
            "hf_model_filename": settings.hf_model_filename,
            "local_model_path": settings.local_model_path,
            "detection_confidence_threshold": settings.detection_confidence_threshold,
        },
    )


def _upgrade_evidence_demo_approved(connection: Connection) -> None:
    """Add the demonstration-viewer approval flag to legacy evidence snapshots."""
    _add_missing_columns(connection, "evidence_snapshots", {"demo_approved": "BOOLEAN DEFAULT 0 NOT NULL"})


def _upgrade_class_and_source_thresholds(connection: Connection) -> None:
    """Add per-class zone-policy and per-source confidence-threshold overrides (FR-DET-05)."""
    _add_missing_columns(connection, "zone_policies", {"class_confidence_thresholds_json": "TEXT DEFAULT '{}' NOT NULL"})
    _add_missing_columns(connection, "camera_sources", {"confidence_threshold_override": "FLOAT"})


def _upgrade_sampling_fps(connection: Connection) -> None:
    """Add the optional per-policy video detector sampling rate."""
    _add_missing_columns(connection, "zone_policies", {"sampling_fps": "FLOAT"})


def _upgrade_event_rule_results(connection: Connection) -> None:
    """Create the durable, explainable per-requirement rule-evaluation record."""
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS event_rule_results ("
            "id VARCHAR(36) PRIMARY KEY, "
            "alert_id VARCHAR(36) NOT NULL, "
            "job_id VARCHAR(36) NOT NULL, "
            "policy_id VARCHAR(36) NOT NULL, "
            "requirement VARCHAR(120) NOT NULL, "
            "persistent BOOLEAN NOT NULL, "
            "non_compliant_count INTEGER NOT NULL, "
            "confidence FLOAT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_event_rule_results_alert_id ON event_rule_results (alert_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_event_rule_results_job_id ON event_rule_results (job_id)"))


def _upgrade_event_acknowledgements(connection: Connection) -> None:
    """Create the append-only alert acknowledgement/resolution history."""
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS event_acknowledgements ("
            "id VARCHAR(36) PRIMARY KEY, "
            "alert_id VARCHAR(36) NOT NULL, "
            "actor_reference VARCHAR(128) NOT NULL, "
            "actor_role VARCHAR(50) NOT NULL, "
            "prior_status VARCHAR(20) NOT NULL, "
            "next_status VARCHAR(20) NOT NULL, "
            "note TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_event_acknowledgements_alert_id ON event_acknowledgements (alert_id)")
    )


def _upgrade_policy_effective_window(connection: Connection) -> None:
    """Add optional policy effective start/end timestamps for time-scoped policy versions."""
    _add_missing_columns(connection, "zone_policies", {"effective_start": "TIMESTAMP", "effective_end": "TIMESTAMP"})


def _upgrade_test_media_retention(connection: Connection) -> None:
    """Add the administrator-approved test-media retention workflow flags to media jobs."""
    _add_missing_columns(
        connection,
        "media_jobs",
        {
            "is_test_media": "BOOLEAN DEFAULT 0 NOT NULL",
            "test_retention_approved": "BOOLEAN DEFAULT 0 NOT NULL",
        },
    )


def _add_missing_columns(connection: Connection, table: str, additions: dict[str, str]) -> None:
    """Add named columns only when upgrading a database that lacks them."""
    columns = {column["name"] for column in inspect(connection).get_columns(table)}
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
