"""Small, dependency-free schema migrations for the PPE POC."""

from __future__ import annotations

from sqlalchemy import Connection, inspect, text

MIGRATION_TABLE = "schema_migrations"
CURRENT_REVISION = "20260909_alert_deduplication_and_audit"


def apply_migrations(connection: Connection) -> None:
    """Apply idempotent additive migrations after SQLAlchemy creates missing tables."""
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} "
            "(revision VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    )
    applied = {
        row[0]
        for row in connection.execute(text(f"SELECT revision FROM {MIGRATION_TABLE}")).all()
    }
    if CURRENT_REVISION in applied:
        return

    columns = {column["name"] for column in inspect(connection).get_columns("compliance_alerts")}
    additions = {
        "deduplication_key": "VARCHAR(255)",
        "occurrence_count": "INTEGER DEFAULT 1 NOT NULL",
        "first_observed_at": "TIMESTAMP",
        "last_observed_at": "TIMESTAMP",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE compliance_alerts ADD COLUMN {name} {definition}"))

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
    connection.execute(
        text(
            f"INSERT INTO {MIGRATION_TABLE} (revision) VALUES (:revision)"
        ),
        {"revision": CURRENT_REVISION},
    )
