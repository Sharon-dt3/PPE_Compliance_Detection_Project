"""Small, dependency-free additive schema migrations for the PPE POC."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Connection, inspect, text

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
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_frame_observations_job_id ON frame_observations (job_id)"))


def _add_missing_columns(connection: Connection, table: str, additions: dict[str, str]) -> None:
    """Add named columns only when upgrading a database that lacks them."""
    columns = {column["name"] for column in inspect(connection).get_columns(table)}
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
