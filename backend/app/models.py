"""Durable persistence entities for the non-identifying PPE safety POC."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all relational application entities."""


class JobStatus(str, Enum):
    """Lifecycle states for submitted media jobs."""

    QUEUED = "queued"
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AlertStatus(str, Enum):
    """Lifecycle states for human-reviewable safety alerts."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApplicationUser(Base):
    """Minimal server-side role assignment for an authenticated identity-provider subject."""

    __tablename__ = "application_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    auth_subject: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Zone(Base):
    """A configured safety area with no identity or personnel information."""

    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ZonePolicy(Base):
    """An immutable policy version governing PPE evaluation for a zone."""

    __tablename__ = "zone_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    helmet_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vest_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.25, nullable=False)
    persistence_frames: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    deduplication_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    evidence_retention_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CameraSource(Base):
    """A manually selected POC media source linked to a safety zone."""

    __tablename__ = "camera_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MediaJob(Base):
    """Private uploaded-media job and its non-identifying aggregate outcome."""

    __tablename__ = "media_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[str] = mapped_column(ForeignKey("camera_sources.id"), nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("zone_policies.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED.value, nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, default="Queued for private processing.", nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    compliant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    non_compliant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(100))
    provider_version: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ComplianceAlert(Base):
    """A deduplicated, non-identifying PPE non-compliance event."""

    __tablename__ = "compliance_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("media_jobs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("camera_sources.id"), nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("zone_policies.id"), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default=AlertStatus.OPEN.value, nullable=False, index=True)
    failed_requirement: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledgement_note: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceSnapshot(Base):
    """Private face-blurred evidence associated with a single reviewable alert."""

    __tablename__ = "evidence_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    alert_id: Mapped[str] = mapped_column(ForeignKey("compliance_alerts.id"), nullable=False, unique=True, index=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    blurred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MetricRollup(Base):
    """A retained aggregate safety metric without media references or identities."""

    __tablename__ = "metric_rollups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("media_jobs.id"), nullable=False, unique=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("camera_sources.id"), nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    observed: Mapped[int] = mapped_column(Integer, nullable=False)
    compliant: Mapped[int] = mapped_column(Integer, nullable=False)
    non_compliant: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown: Mapped[int] = mapped_column(Integer, nullable=False)
    shift: Mapped[str] = mapped_column(String(50), default="unspecified", nullable=False, index=True)
    rule_key: Mapped[str] = mapped_column(String(120), default="all_required_ppe", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FrameObservation(Base):
    """A short-lived, non-identifying summary of one sampled processing frame."""

    __tablename__ = "frame_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("media_jobs.id"), nullable=False, index=True)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    person_count: Mapped[int] = mapped_column(Integer, nullable=False)
    compliant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    non_compliant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ModelEvaluation(Base):
    """Class-level benchmark metadata for a candidate PPE inference model."""

    __tablename__ = "model_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    licence_status: Mapped[str] = mapped_column(String(120), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(50), default="poc_only", nullable=False)
    class_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    effective_sampling_rate: Mapped[float | None] = mapped_column(Float)
    limitations: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditEvent(Base):
    """An immutable operational event without raw media, biometric, or HR data."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
