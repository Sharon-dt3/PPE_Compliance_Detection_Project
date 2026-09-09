"""Aggregate-only reporting and export helpers for the PPE safety POC."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import ComplianceAlert, MetricRollup


@dataclass(frozen=True)
class ReportFilters:
    """Optional aggregate report constraints with no person-level fields."""

    zone_id: str | None = None
    source_id: str | None = None
    shift: str | None = None
    rule_key: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class AggregateReportingService:
    """Produce scoped, non-identifying safety metrics and export-ready aggregates."""

    def compliance_totals(self, session: Session, filters: ReportFilters) -> dict[str, int | float | None]:
        """Calculate transparent compliance counts and a safe denominator."""
        statement = self._rollup_statement(filters).with_only_columns(
            func.coalesce(func.sum(MetricRollup.compliant), 0),
            func.coalesce(func.sum(MetricRollup.non_compliant), 0),
            func.coalesce(func.sum(MetricRollup.unknown), 0),
        )
        compliant, non_compliant, unknown = (int(value) for value in session.execute(statement).one())
        observed = compliant + non_compliant
        return {
            "observed": observed,
            "compliant": compliant,
            "non_compliant": non_compliant,
            "unknown": unknown,
            "compliance_rate": round(compliant / observed * 100, 1) if observed else None,
        }

    def alert_totals(self, session: Session, filters: ReportFilters) -> dict[str, int | float | dict[str, int]]:
        """Calculate lifecycle counts and review-time metrics without alert evidence."""
        statement = select(ComplianceAlert).where(ComplianceAlert.created_at.is_not(None))
        statement = self._apply_alert_filters(statement, filters)
        alerts = session.scalars(statement).all()
        statuses = {name: 0 for name in ("open", "acknowledged", "resolved", "expired", "cancelled")}
        acknowledgement_seconds: list[float] = []
        resolution_seconds: list[float] = []
        for alert in alerts:
            statuses[alert.status] = statuses.get(alert.status, 0) + 1
            if alert.acknowledged_at is not None:
                acknowledgement_seconds.append((alert.acknowledged_at - alert.created_at).total_seconds())
            if alert.resolved_at is not None:
                resolution_seconds.append((alert.resolved_at - alert.created_at).total_seconds())
        return {
            "total": len(alerts),
            "states": statuses,
            "average_acknowledgement_minutes": self._average_minutes(acknowledgement_seconds),
            "average_resolution_minutes": self._average_minutes(resolution_seconds),
        }

    def daily_trend(self, session: Session, filters: ReportFilters) -> list[dict[str, int | float | str | None]]:
        """Return daily aggregate trend points and explicit unknown-data counts."""
        statement = self._rollup_statement(filters).with_only_columns(
            func.date(MetricRollup.created_at).label("day"),
            func.coalesce(func.sum(MetricRollup.compliant), 0),
            func.coalesce(func.sum(MetricRollup.non_compliant), 0),
            func.coalesce(func.sum(MetricRollup.unknown), 0),
        ).group_by(func.date(MetricRollup.created_at)).order_by(func.date(MetricRollup.created_at))
        return [
            self._trend_row(str(day), int(compliant), int(non_compliant), int(unknown))
            for day, compliant, non_compliant, unknown in session.execute(statement).all()
        ]

    def export_rows(self, session: Session, filters: ReportFilters) -> list[dict[str, int | str]]:
        """Return aggregate rows only; no filenames, evidence, identities, or media references."""
        statement = self._rollup_statement(filters).with_only_columns(
            MetricRollup.zone_id,
            MetricRollup.source_id,
            MetricRollup.shift,
            MetricRollup.rule_key,
            func.date(MetricRollup.created_at).label("date"),
            func.sum(MetricRollup.compliant),
            func.sum(MetricRollup.non_compliant),
            func.sum(MetricRollup.unknown),
        ).group_by(
            MetricRollup.zone_id,
            MetricRollup.source_id,
            MetricRollup.shift,
            MetricRollup.rule_key,
            func.date(MetricRollup.created_at),
        ).order_by(func.date(MetricRollup.created_at))
        return [
            {
                "date": str(row.date),
                "zone_id": row.zone_id,
                "source_id": row.source_id,
                "shift": row.shift,
                "rule_key": row.rule_key,
                "compliant": int(row.compliant or 0),
                "non_compliant": int(row.non_compliant or 0),
                "unknown": int(row.unknown or 0),
            }
            for row in session.execute(statement).all()
        ]

    def _rollup_statement(self, filters: ReportFilters) -> Select:
        """Build a query scoped to permitted aggregate dimensions."""
        statement = select(MetricRollup)
        if filters.zone_id:
            statement = statement.where(MetricRollup.zone_id == filters.zone_id)
        if filters.source_id:
            statement = statement.where(MetricRollup.source_id == filters.source_id)
        if filters.shift:
            statement = statement.where(MetricRollup.shift == filters.shift)
        if filters.rule_key:
            statement = statement.where(MetricRollup.rule_key == filters.rule_key)
        if filters.start_at:
            statement = statement.where(MetricRollup.created_at >= filters.start_at)
        if filters.end_at:
            statement = statement.where(MetricRollup.created_at <= filters.end_at)
        return statement

    @staticmethod
    def _apply_alert_filters(statement: Select, filters: ReportFilters) -> Select:
        """Apply shared source, zone, and time scope to alert metrics."""
        if filters.zone_id:
            statement = statement.where(ComplianceAlert.zone_id == filters.zone_id)
        if filters.source_id:
            statement = statement.where(ComplianceAlert.source_id == filters.source_id)
        if filters.start_at:
            statement = statement.where(ComplianceAlert.created_at >= filters.start_at)
        if filters.end_at:
            statement = statement.where(ComplianceAlert.created_at <= filters.end_at)
        return statement

    @staticmethod
    def _average_minutes(values: list[float]) -> float | None:
        """Convert a collection of lifecycle durations into an optional minute average."""
        return round(sum(values) / len(values) / 60, 1) if values else None

    @staticmethod
    def _trend_row(day: str, compliant: int, non_compliant: int, unknown: int) -> dict[str, int | float | str | None]:
        """Format one transparent trend point with the correct assessed denominator."""
        observed = compliant + non_compliant
        return {
            "date": day,
            "compliant": compliant,
            "non_compliant": non_compliant,
            "unknown": unknown,
            "observed": observed,
            "compliance_rate": round(compliant / observed * 100, 1) if observed else None,
        }
