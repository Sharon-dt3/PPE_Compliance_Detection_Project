"""FastAPI entry point for the privacy-first PPE Compliance Detection POC."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_actor_audit_event
from app.auth import AuthenticatedActor, Role, current_actor, require_role
from app.config import settings
from app.database import get_session, initialise_database
from app.evidence import check_face_detector_readiness
from app.logging_config import configure_logging, set_correlation_id
from app.media_validation import validate_media_upload, validate_video_file
from app.models import (
    ApplicationUser,
    AuditEvent,
    CameraSource,
    ComplianceAlert,
    EventAcknowledgement,
    EventRuleResult,
    EvidenceSnapshot,
    FrameObservation,
    JobStatus,
    MediaJob,
    MetricRollup,
    ModelEvaluation,
    PlatformSettings,
    Zone,
    ZonePolicy,
)
from app.platform_settings import get_platform_settings
from app.reporting import AggregateReportingService, ReportFilters
from app.storage import PrivateMediaStorage
from app.worker import process_media_job_task

logger = logging.getLogger(__name__)


class PolicyResponse(BaseModel):
    """Public representation of one active zone policy."""

    version: int
    helmet_required: bool
    vest_required: bool
    confidence_threshold: float
    class_confidence_thresholds: dict[str, float]
    persistence_frames: int
    deduplication_seconds: int
    sampling_fps: float | None
    effective_start: datetime | None
    effective_end: datetime | None


class ZoneResponse(BaseModel):
    """Public representation of a configured safety zone."""

    id: str
    name: str
    description: str
    policy: PolicyResponse


class SourceResponse(BaseModel):
    """Public representation of an enabled camera source."""

    id: str
    name: str
    zone_id: str
    confidence_threshold_override: float | None


class ZoneRequest(BaseModel):
    """Administrator input for a non-identifying safety zone."""

    name: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=3, max_length=1000)
    enabled: bool = True


class SourceRequest(BaseModel):
    """Administrator input for a manually selectable camera source."""

    name: str = Field(min_length=3, max_length=120)
    zone_id: str = Field(min_length=1, max_length=36)
    enabled: bool = True
    confidence_threshold_override: float | None = Field(
        default=None, ge=0, le=1, description="Replaces the zone policy's flat threshold for this source only (FR-DET-05)."
    )


class SourceUpdateRequest(BaseModel):
    """Optional administrator changes to a camera-source configuration."""

    name: str | None = Field(default=None, min_length=3, max_length=120)
    zone_id: str | None = Field(default=None, min_length=1, max_length=36)
    enabled: bool | None = None
    confidence_threshold_override: float | None = Field(default=None, ge=0, le=1)
    clear_confidence_threshold_override: bool = Field(
        default=False, description="Set true to remove a previously configured override and fall back to the zone policy."
    )


class PolicyRequest(BaseModel):
    """Administrator input for a new immutable versioned zone PPE policy."""

    helmet_required: bool = True
    vest_required: bool = True
    confidence_threshold: float = Field(default=0.25, ge=0, le=1)
    class_confidence_thresholds: dict[str, float] = Field(
        default_factory=dict,
        description="Optional per-label override, e.g. {\"no_helmet\": 0.4}; a label not listed uses confidence_threshold (FR-DET-05).",
    )
    persistence_frames: int = Field(default=3, ge=1, le=60)
    deduplication_seconds: int = Field(default=60, ge=0, le=86_400)
    evidence_retention_hours: int = Field(default=48, ge=24, le=72)
    sampling_fps: float | None = Field(
        default=None, gt=0, le=30, description="Target detector sampling rate for video sources, e.g. 2-5 FPS; unset processes every frame."
    )
    effective_start: datetime | None = Field(
        default=None, description="Optional inclusive UTC timestamp when this policy version becomes effective."
    )
    effective_end: datetime | None = Field(
        default=None, description="Optional inclusive UTC timestamp after which this policy version is no longer effective."
    )
    active: bool = True

    @field_validator("class_confidence_thresholds")
    @classmethod
    def _validate_class_thresholds(cls, value: dict[str, float]) -> dict[str, float]:
        """Reject unsupported labels and out-of-range thresholds before they reach storage."""
        allowed_labels = {"person", "helmet", "no_helmet", "vest", "no_vest", "gloves", "glasses"}
        for label, threshold in value.items():
            if label not in allowed_labels:
                raise ValueError(f"Unsupported PPE label '{label}'.")
            if not 0 <= threshold <= 1:
                raise ValueError(f"Threshold for '{label}' must be between 0 and 1.")
        return value

    @field_validator("effective_end")
    @classmethod
    def _validate_effective_window(cls, value: datetime | None, info) -> datetime | None:
        """Reject an effective end that is not after the effective start, when both are set."""
        effective_start = info.data.get("effective_start")
        if value is not None and effective_start is not None and value <= effective_start:
            raise ValueError("effective_end must be after effective_start.")
        return value


class FrameObservationResponse(BaseModel):
    """Privacy-safe sampled-frame counts and failure-confidence range."""

    frame_index: int
    person_count: int
    compliant_count: int
    non_compliant_count: int
    unknown_count: int
    confidence_summary: dict[str, dict[str, float]]


class MediaJobResponse(BaseModel):
    """Non-identifying persisted media-job status and aggregate results."""

    id: str
    source_id: str
    zone_id: str
    filename: str
    status: str
    submitted_at: datetime
    completed_at: datetime | None
    compliant_count: int
    non_compliant_count: int
    unknown_count: int
    message: str
    failure_code: str | None
    is_test_media: bool
    test_retention_approved: bool


class AlertActionRequest(BaseModel):
    """Supervisor-supplied safety intervention or resolution note."""

    note: str = Field(min_length=3, max_length=500, description="Non-identifying safety intervention or outcome note.")


class TestMediaRetentionApprovalRequest(BaseModel):
    """Administrator confirmation extending retention for one test-media job.

    Only test media flagged at upload time (``is_test_media=True``) can go through this
    approval workflow; approving retention for real operational media is not permitted.
    """

    retention_hours: int = Field(
        ge=1, le=720, description="Administrator-approved retention duration in hours for this test-media job."
    )
    justification: str = Field(
        min_length=3, max_length=500, description="Why this test-media job's private retention was extended."
    )


class AlertResponse(BaseModel):
    """Non-identifying alert information available to authorized reviewers."""

    id: str
    source_id: str
    zone_id: str
    status: str
    failed_requirement: str
    confidence: float
    occurrence_count: int
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledgement_note: str | None
    resolved_at: datetime | None
    resolution_note: str | None
    evidence_available: bool
    evidence_message: str


class RuleResultResponse(BaseModel):
    """One durable, explainable rule-evaluation record behind an alert."""

    id: str
    requirement: str
    persistent: bool
    non_compliant_count: int
    confidence: float | None
    created_at: datetime


class AcknowledgementResponse(BaseModel):
    """One append-only human-review action taken on an alert."""

    id: str
    actor_role: str
    prior_status: str
    next_status: str
    note: str
    created_at: datetime


class ComplianceReport(BaseModel):
    """Aggregate dashboard values that intentionally exclude worker identities."""

    observed: int
    compliant: int
    non_compliant: int
    unknown: int
    compliance_rate: float | None
    open_alerts: int
    disclaimer: str


class TrendPoint(BaseModel):
    """One day of transparent aggregate compliance trend data."""

    date: str
    observed: int
    compliant: int
    non_compliant: int
    unknown: int
    compliance_rate: float | None


class AlertMetricsReport(BaseModel):
    """Aggregate alert lifecycle counts and optional review-time measures."""

    total: int
    states: dict[str, int]
    average_acknowledgement_minutes: float | None
    average_resolution_minutes: float | None
    disclaimer: str


class AggregateExportRequest(BaseModel):
    """Filters for a CSV export containing aggregate safety data only."""

    zone_id: str | None = Field(default=None, description="Optional configured zone identifier.")
    source_id: str | None = Field(default=None, description="Optional configured source identifier.")
    shift: str | None = Field(default=None, max_length=50, description="Optional configured shift label.")
    rule_key: str | None = Field(default=None, max_length=120, description="Optional PPE rule label.")
    start_at: datetime | None = Field(default=None, description="Inclusive report start timestamp.")
    end_at: datetime | None = Field(default=None, description="Inclusive report end timestamp.")


class ModelEvaluationRequest(BaseModel):
    """Class-level candidate-model benchmark metadata for POC review."""

    provider: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=160)
    model_version: str = Field(min_length=1, max_length=100)
    dataset_reference: str = Field(min_length=1, max_length=255, description="Approved benchmark-set reference; no raw media location.")
    licence_status: str = Field(min_length=1, max_length=120)
    approval_state: str = Field(default="poc_only", max_length=50)
    class_metrics: dict[str, dict[str, float | int]] = Field(description="Per-class TP, FP, FN, precision, recall, and F1 values.")
    latency_ms: float | None = Field(default=None, ge=0)
    effective_sampling_rate: float | None = Field(default=None, ge=0)
    limitations: str = Field(min_length=3, max_length=2000)


class ModelEvaluationResponse(ModelEvaluationRequest):
    """Safe model-evaluation output that preserves class-level limitations."""

    id: str
    created_at: datetime


class MeResponse(BaseModel):
    """The authenticated caller's own server-resolved identity and role."""

    reference: str
    role: str


class ApplicationUserResponse(BaseModel):
    """Administrator view of one server-side role assignment."""

    id: str
    auth_subject: str
    role: str
    enabled: bool
    created_at: datetime


class ApplicationUserCreateRequest(BaseModel):
    """Administrator input assigning a role to a verified identity-provider subject."""

    auth_subject: str = Field(min_length=1, max_length=128, description="The identity provider's stable subject (JWT `sub`) claim.")
    role: Role
    enabled: bool = True


class ApplicationUserUpdateRequest(BaseModel):
    """Administrator changes to an existing role assignment."""

    role: Role | None = None
    enabled: bool | None = None


class RetentionSettingsResponse(BaseModel):
    """Current administrator-configurable retention durations."""

    raw_media_retention_hours: int
    frame_observation_retention_hours: int
    updated_at: datetime


class RetentionSettingsUpdateRequest(BaseModel):
    """Administrator changes to global retention durations.

    Face-blurred evidence retention is intentionally excluded: it remains set per zone
    policy version (24-72 hours) rather than as a global override.
    """

    raw_media_retention_hours: int | None = Field(default=None, ge=1, le=168, description="Hours before a raw upload is deleted.")
    frame_observation_retention_hours: int | None = Field(
        default=None, ge=1, le=168, description="Hours before short-lived frame/person summaries are deleted."
    )


class InferenceSettingsResponse(BaseModel):
    """Current administrator-configurable PPE detection provider configuration."""

    detection_provider: str
    demo_mode: bool
    hf_model_repository: str
    hf_model_filename: str
    local_model_path: str
    detection_confidence_threshold: float
    updated_at: datetime


class InferenceSettingsUpdateRequest(BaseModel):
    """Administrator changes to the active PPE detection provider configuration.

    Real inference stays gated behind an explicit ``demo_mode=false``; misconfiguration
    fails a media job safely rather than falling back to a substitute provider.
    """

    detection_provider: str | None = Field(default=None, pattern="^(demo|ultralytics)$")
    demo_mode: bool | None = None
    hf_model_repository: str | None = Field(default=None, max_length=200)
    hf_model_filename: str | None = Field(default=None, max_length=200)
    local_model_path: str | None = Field(default=None, max_length=500)
    detection_confidence_threshold: float | None = Field(default=None, ge=0, le=1)


class AuditEventResponse(BaseModel):
    """Restricted, non-identifying operational audit event representation."""

    id: str
    event_type: str
    entity_type: str
    entity_id: str
    actor_role: str
    detail: str
    created_at: datetime


app = FastAPI(
    title=settings.app_name,
    description=(
        "Safety-first POC API. Outputs are indicative, non-identifying decision-support "
        "signals and must not be used for autonomous enforcement."
    ),
    version="0.4.0",
    openapi_tags=[
        {"name": "Health", "description": "Service health and readiness."},
        {"name": "Configuration", "description": "Configured safety zones and sources."},
        {"name": "Administration", "description": "Server-side identity role assignments."},
        {"name": "Media jobs", "description": "Private manual-upload media processing."},
        {"name": "Alerts", "description": "Reviewable non-identifying safety alerts and privacy-gated evidence."},
        {"name": "Reports", "description": "Aggregate safety metrics only."},
        {"name": "Model evaluation", "description": "Transparent POC benchmark metadata and limitations."},
        {"name": "Audit", "description": "Restricted non-identifying operating records."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Demo-Role"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def correlate_request(request: Request, call_next):
    """Attach one correlation id to every request, its logs, and its response header.

    Reuses an incoming ``X-Request-Id`` when a caller already has one (e.g. propagated from
    an upstream gateway); otherwise generates one. This is the shared mechanism behind
    Phase 1's "correlation/request IDs in responses and logs" requirement -- every log line
    emitted while handling this request carries the same id via ``logging_config``.

    Deliberately does not reset the correlation id back to ``None`` before returning: the
    response is only streamed to the client, and uvicorn's own access-log line written,
    after this coroutine returns, so resetting here would blank the id from that final log
    line. Each request runs in its own asyncio task, so leaving it set does not leak the id
    into a sibling request.
    """
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    set_correlation_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Return a safe, generic 5xx body for any error not already an HTTPException.

    Completes the status-code contract as shared middleware: callers always receive a
    consistent JSON error shape, and the real exception (never raw media, storage paths, or
    credentials) is logged server-side against the request's correlation id only.
    """
    logger.exception("Unhandled error while processing a request.")
    return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred. Please try again or contact support."})


@app.on_event("startup")
async def startup() -> None:
    """Configure structured logging, then initialise the schema before accepting requests."""
    configure_logging()
    initialise_database()
    readiness = check_face_detector_readiness()
    if readiness.status == "ready":
        logger.info("Face-detector privacy gate verified at startup: %s", readiness.detail)
    elif readiness.status == "not_configured":
        logger.warning("Face-detector privacy gate is not configured at startup: %s", readiness.detail)
    else:
        logger.error("Face-detector privacy gate is unavailable at startup: %s", readiness.detail)


@app.get("/health", tags=["Health"], summary="Check service health")
# PUBLIC_INTERFACE
def health_check() -> dict[str, str]:
    """Return service health for local development and deployment checks."""
    return {"status": "healthy"}


@app.get(
    "/health/face-detector",
    tags=["Health"],
    summary="Check the mandatory face-detector privacy-gate readiness",
)
# PUBLIC_INTERFACE
def face_detector_health() -> dict[str, str]:
    """Report whether the configured face-detector model loads and runs inference.

    Verifies Phase 1's "Day 1" requirement independently of any processed media: this
    confirms the OpenCV DNN model itself is ready, not that the Phase 7 blur pipeline has
    already run. A ``not_configured`` or ``unavailable`` status means every evidence
    generation attempt will fail closed (no unblurred evidence is ever produced instead).

    Returns:
        A JSON object with ``status`` (``ready``, ``not_configured``, or ``unavailable``)
        and a human-readable ``detail`` message; never raises for an unready model.
    """
    readiness = check_face_detector_readiness()
    return {"status": readiness.status, "detail": readiness.detail}


@app.get("/api/v1/me", response_model=MeResponse, tags=["Configuration"], summary="Get the authenticated caller's role")
# PUBLIC_INTERFACE
def get_me(actor: AuthenticatedActor = Depends(current_actor)) -> MeResponse:
    """Return the caller's server-resolved reference and role for client-side navigation only.

    This is not an authorization decision: every route independently enforces its own
    required roles server-side regardless of what this endpoint returns.
    """
    return MeResponse(reference=actor.reference, role=actor.role.value)


@app.get("/api/v1/users", response_model=list[ApplicationUserResponse], tags=["Administration"], summary="List role assignments")
# PUBLIC_INTERFACE
def list_users(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> list[ApplicationUserResponse]:
    """Return every configured server-side role assignment for administrator review."""
    users = session.scalars(select(ApplicationUser).order_by(ApplicationUser.created_at)).all()
    return [_user_response(user) for user in users]


@app.post(
    "/api/v1/users",
    response_model=ApplicationUserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Administration"],
    summary="Assign a role to an identity-provider subject",
)
# PUBLIC_INTERFACE
def create_user(
    request: ApplicationUserCreateRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> ApplicationUserResponse:
    """Create one server-side role assignment for a verified identity-provider subject.

    The role a client-supplied JWT claims is never trusted; only a row created here through
    an authenticated administrator determines what an identity is permitted to do.
    """
    if session.scalar(select(ApplicationUser).where(ApplicationUser.auth_subject == request.auth_subject)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This identity already has an assigned role.")
    user = ApplicationUser(auth_subject=request.auth_subject, role=request.role.value, enabled=request.enabled)
    session.add(user)
    session.flush()
    record_actor_audit_event(
        session, "user.role_assigned", "application_user", user.id, actor, f"Role {request.role.value} assigned."
    )
    session.commit()
    return _user_response(user)


@app.patch(
    "/api/v1/users/{user_id}",
    response_model=ApplicationUserResponse,
    tags=["Administration"],
    summary="Update a role assignment",
)
# PUBLIC_INTERFACE
def update_user(
    user_id: str,
    request: ApplicationUserUpdateRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> ApplicationUserResponse:
    """Update an existing role assignment's role or enabled state, fully audited."""
    user = session.get(ApplicationUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This role assignment does not exist.")
    if user.auth_subject == actor.reference and request.enabled is False:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot disable your own administrator access.")

    changes: list[str] = []
    if request.role is not None and request.role.value != user.role:
        user.role = request.role.value
        changes.append(f"role={request.role.value}")
    if request.enabled is not None and request.enabled != user.enabled:
        user.enabled = request.enabled
        changes.append(f"enabled={request.enabled}")
    if changes:
        record_actor_audit_event(session, "user.role_updated", "application_user", user.id, actor, "; ".join(changes))
    session.commit()
    return _user_response(user)


@app.get(
    "/api/v1/settings/retention",
    response_model=RetentionSettingsResponse,
    tags=["Administration"],
    summary="Get retention settings",
)
# PUBLIC_INTERFACE
def get_retention_settings(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER)),
) -> RetentionSettingsResponse:
    """Return the active global retention durations for administrator or governance review."""
    config = get_platform_settings(session)
    session.commit()
    return _retention_settings_response(config)


@app.patch(
    "/api/v1/settings/retention",
    response_model=RetentionSettingsResponse,
    tags=["Administration"],
    summary="Update retention settings",
)
# PUBLIC_INTERFACE
def update_retention_settings(
    request: RetentionSettingsUpdateRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> RetentionSettingsResponse:
    """Update global retention durations at runtime without a redeploy, fully audited."""
    config = get_platform_settings(session)
    changes: list[str] = []
    if request.raw_media_retention_hours is not None and request.raw_media_retention_hours != config.raw_media_retention_hours:
        config.raw_media_retention_hours = request.raw_media_retention_hours
        changes.append(f"raw_media_retention_hours={request.raw_media_retention_hours}")
    if (
        request.frame_observation_retention_hours is not None
        and request.frame_observation_retention_hours != config.frame_observation_retention_hours
    ):
        config.frame_observation_retention_hours = request.frame_observation_retention_hours
        changes.append(f"frame_observation_retention_hours={request.frame_observation_retention_hours}")
    if changes:
        config.updated_at = datetime.now(UTC)
        record_actor_audit_event(session, "configuration.retention_updated", "platform_settings", config.id, actor, "; ".join(changes))
    session.commit()
    return _retention_settings_response(config)


@app.get(
    "/api/v1/settings/inference",
    response_model=InferenceSettingsResponse,
    tags=["Administration"],
    summary="Get inference provider configuration",
)
# PUBLIC_INTERFACE
def get_inference_settings(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR, Role.MODEL_EVALUATOR)),
) -> InferenceSettingsResponse:
    """Return the active PPE detection provider configuration for administrator or evaluator review."""
    config = get_platform_settings(session)
    session.commit()
    return _inference_settings_response(config)


@app.patch(
    "/api/v1/settings/inference",
    response_model=InferenceSettingsResponse,
    tags=["Administration"],
    summary="Update inference provider configuration",
)
# PUBLIC_INTERFACE
def update_inference_settings(
    request: InferenceSettingsUpdateRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> InferenceSettingsResponse:
    """Update the active PPE detection provider configuration at runtime, fully audited.

    A misconfigured combination (for example, an unreachable Hugging Face repository) is
    not validated here: it fails the next media job safely and visibly instead, per the
    existing detector fail-closed boundary.
    """
    config = get_platform_settings(session)
    changes: list[str] = []
    for field_name in (
        "detection_provider",
        "demo_mode",
        "hf_model_repository",
        "hf_model_filename",
        "local_model_path",
        "detection_confidence_threshold",
    ):
        new_value = getattr(request, field_name)
        if new_value is not None and new_value != getattr(config, field_name):
            setattr(config, field_name, new_value)
            changes.append(f"{field_name}={new_value}")
    if changes:
        config.updated_at = datetime.now(UTC)
        record_actor_audit_event(session, "configuration.inference_updated", "platform_settings", config.id, actor, "; ".join(changes))
    session.commit()
    return _inference_settings_response(config)


@app.get("/api/v1/zones", response_model=list[ZoneResponse], tags=["Configuration"], summary="List safety zones")
# PUBLIC_INTERFACE
def list_zones(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(
        require_role(
            Role.SUPERVISOR,
            Role.HSE_MANAGER,
            Role.ADMINISTRATOR,
            Role.GOVERNANCE_REVIEWER,
            Role.DEMO_VIEWER,
        )
    ),
) -> list[ZoneResponse]:
    """Return configured zones and their active PPE policies to permitted users."""
    zones = session.scalars(select(Zone).where(Zone.enabled.is_(True)).order_by(Zone.name)).all()
    results: list[ZoneResponse] = []
    for zone in zones:
        policy = session.scalar(select(ZonePolicy).where(ZonePolicy.zone_id == zone.id, ZonePolicy.active.is_(True)))
        if policy is not None:
            results.append(_zone_response(zone, policy))
    return results


@app.post(
    "/api/v1/zones",
    response_model=ZoneResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Configuration"],
    summary="Create a safety zone",
)
# PUBLIC_INTERFACE
def create_zone(
    request: ZoneRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> ZoneResponse:
    """Create a safety zone and its initial configurable policy version."""
    if session.scalar(select(Zone).where(Zone.name == request.name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A safety zone with this name already exists.")
    zone = Zone(**request.model_dump())
    session.add(zone)
    session.flush()
    policy = ZonePolicy(zone_id=zone.id, version=1)
    session.add(policy)
    record_actor_audit_event(session, "configuration.zone_created", "zone", zone.id, actor, "Safety zone and initial policy created.")
    session.commit()
    return _zone_response(zone, policy)


@app.get("/api/v1/sources", response_model=list[SourceResponse], tags=["Configuration"], summary="List camera sources")
# PUBLIC_INTERFACE
def list_sources(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> list[SourceResponse]:
    """Return enabled manually selectable sources for private POC media submission."""
    sources = session.scalars(select(CameraSource).where(CameraSource.enabled.is_(True)).order_by(CameraSource.name)).all()
    return [
        SourceResponse(id=item.id, name=item.name, zone_id=item.zone_id, confidence_threshold_override=item.confidence_threshold_override)
        for item in sources
    ]


@app.post(
    "/api/v1/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Configuration"],
    summary="Create a camera source",
)
# PUBLIC_INTERFACE
def create_source(
    request: SourceRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> SourceResponse:
    """Create a manually uploaded-media source linked to an existing safety zone."""
    if session.get(Zone, request.zone_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured safety zone not found.")
    if session.scalar(select(CameraSource).where(CameraSource.name == request.name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A camera source with this name already exists.")
    source = CameraSource(**request.model_dump())
    session.add(source)
    session.flush()
    record_actor_audit_event(session, "configuration.source_created", "camera_source", source.id, actor, "Camera source created.")
    session.commit()
    return SourceResponse(
        id=source.id, name=source.name, zone_id=source.zone_id,
        confidence_threshold_override=source.confidence_threshold_override,
    )


@app.get("/api/v1/sources/{source_id}", response_model=SourceResponse, tags=["Configuration"], summary="Get camera source configuration")
# PUBLIC_INTERFACE
def get_source(
    source_id: str,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> SourceResponse:
    """Return one permitted camera-source configuration without storage details."""
    source = session.get(CameraSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured camera source not found.")
    return SourceResponse(
        id=source.id, name=source.name, zone_id=source.zone_id,
        confidence_threshold_override=source.confidence_threshold_override,
    )


@app.patch("/api/v1/sources/{source_id}", response_model=SourceResponse, tags=["Configuration"], summary="Update a camera source")
# PUBLIC_INTERFACE
def update_source(
    source_id: str,
    request: SourceUpdateRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> SourceResponse:
    """Apply administrator-approved camera-source changes and record an audit entry."""
    source = session.get(CameraSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured camera source not found.")
    changes = request.model_dump(exclude_none=True, exclude={"clear_confidence_threshold_override"})
    if request.clear_confidence_threshold_override:
        changes["confidence_threshold_override"] = None
    if not changes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one source property must be supplied.")
    if "zone_id" in changes and session.get(Zone, changes["zone_id"]) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured safety zone not found.")
    if "name" in changes and changes["name"] != source.name:
        if session.scalar(select(CameraSource).where(CameraSource.name == changes["name"])):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A camera source with this name already exists.")
    for field, value in changes.items():
        setattr(source, field, value)
    record_actor_audit_event(session, "configuration.source_updated", "camera_source", source.id, actor, "Camera source configuration changed.")
    session.commit()
    return SourceResponse(
        id=source.id, name=source.name, zone_id=source.zone_id,
        confidence_threshold_override=source.confidence_threshold_override,
    )


@app.patch("/api/v1/zones/{zone_id}/policy", response_model=PolicyResponse, tags=["Configuration"], summary="Create and activate a zone policy version")
# PUBLIC_INTERFACE
def create_zone_policy_version(
    zone_id: str,
    request: PolicyRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> PolicyResponse:
    """Create an immutable policy version, superseding the active version only when requested."""
    if session.get(Zone, zone_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured safety zone not found.")
    latest_version = session.scalar(select(func.max(ZonePolicy.version)).where(ZonePolicy.zone_id == zone_id)) or 0
    if request.active:
        for policy in session.scalars(select(ZonePolicy).where(ZonePolicy.zone_id == zone_id, ZonePolicy.active.is_(True))).all():
            policy.active = False
    policy = ZonePolicy(
        zone_id=zone_id,
        version=int(latest_version) + 1,
        class_confidence_thresholds_json=json.dumps(request.class_confidence_thresholds),
        **request.model_dump(exclude={"class_confidence_thresholds"}),
    )
    session.add(policy)
    session.flush()
    record_actor_audit_event(session, "configuration.policy_version_created", "zone_policy", policy.id, actor, "Versioned PPE policy created.")
    session.commit()
    return _policy_response(policy)


@app.post(
    "/api/v1/media-jobs",
    response_model=MediaJobResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Media jobs"],
    summary="Create a private media processing job",
)
# PUBLIC_INTERFACE
async def create_media_job(
    source_id: str,
    file: UploadFile = File(description="Private POC JPEG, PNG, MP4, or MOV upload."),
    is_test_media: bool = False,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> MediaJobResponse:
    """Validate and persist a manual upload, then dispatch private processing through the worker.

    Args:
        source_id: Enabled source whose zone selects the active safety policy.
        file: Uploaded CCTV still or recorded video clip.
        is_test_media: Marks this upload as non-operational test/demo media. Test media is
            eligible for the administrator-approved retention-extension workflow; real
            operational media is not.

    Returns:
        A queued non-identifying job record. Private storage references are never returned.
    """
    source = session.get(CameraSource, source_id)
    if source is None or not source.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured camera source not found.")
    policy = session.scalar(select(ZonePolicy).where(ZonePolicy.zone_id == source.zone_id, ZonePolicy.active.is_(True)))
    if policy is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The selected source has no active safety policy.")

    in_flight_jobs = session.scalar(
        select(func.count(MediaJob.id)).where(MediaJob.status.in_([JobStatus.QUEUED.value, JobStatus.PROCESSING.value]))
    )
    if in_flight_jobs is not None and in_flight_jobs >= settings.max_concurrent_jobs:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The maximum number of concurrently processing media jobs has been reached. Retry shortly.",
        )

    content = await file.read(settings.max_upload_bytes + 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The uploaded file exceeds the configured size limit.")

    filename = file.filename or "unnamed-upload"
    validated = validate_media_upload(filename, file.content_type, content)
    storage = PrivateMediaStorage()
    storage_key = storage.save(content, filename)
    private_path = str(storage.root_path(storage_key))
    try:
        if validated.media_kind in {"mp4", "mov"}:
            validate_video_file(private_path)
    except ValueError as error:
        storage.delete(storage_key)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

    job = MediaJob(
        source_id=source.id,
        zone_id=source.zone_id,
        policy_id=policy.id,
        filename=filename,
        content_type=validated.content_type,
        storage_key=storage_key,
        status=JobStatus.QUEUED.value,
        is_test_media=is_test_media,
        expires_at=datetime.now(UTC) + timedelta(hours=get_platform_settings(session).raw_media_retention_hours),
    )
    session.add(job)
    session.flush()
    record_actor_audit_event(session, "media_job.created", "media_job", job.id, actor, "Approved private upload accepted.")
    session.commit()
    session.refresh(job)

    try:
        process_media_job_task.delay(job.id)
    except Exception:
        job.message = "Queued safely. The worker is currently unavailable; processing will resume when it is restored."
        session.commit()
    return _job_response(job)


@app.get("/api/v1/media-jobs", response_model=list[MediaJobResponse], tags=["Media jobs"], summary="List media processing jobs")
# PUBLIC_INTERFACE
def list_media_jobs(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> list[MediaJobResponse]:
    """Return safe status summaries for recent private media-processing jobs."""
    jobs = session.scalars(select(MediaJob).order_by(MediaJob.submitted_at.desc()).limit(25)).all()
    return [_job_response(item) for item in jobs]


@app.get("/api/v1/media-jobs/{job_id}", response_model=MediaJobResponse, tags=["Media jobs"], summary="Get media job status")
# PUBLIC_INTERFACE
def get_media_job(
    job_id: str,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> MediaJobResponse:
    """Return a safe processing summary for one persisted media job."""
    job = _get_job_or_404(session, job_id)
    return _job_response(job)


@app.get(
    "/api/v1/media-jobs/{job_id}/frames",
    response_model=list[FrameObservationResponse],
    tags=["Media jobs"],
    summary="Get privacy-safe frame observation summaries",
)
# PUBLIC_INTERFACE
def list_media_job_frames(
    job_id: str,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> list[FrameObservationResponse]:
    """Return non-identifying sampled-frame counts and confidence ranges for one job."""
    _get_job_or_404(session, job_id)
    observations = session.scalars(
        select(FrameObservation).where(FrameObservation.job_id == job_id).order_by(FrameObservation.frame_index)
    ).all()
    return [
        FrameObservationResponse(
            frame_index=observation.frame_index,
            person_count=observation.person_count,
            compliant_count=observation.compliant_count,
            non_compliant_count=observation.non_compliant_count,
            unknown_count=observation.unknown_count,
            confidence_summary=json.loads(observation.confidence_summary),
        )
        for observation in observations
    ]


@app.post("/api/v1/media-jobs/{job_id}/cancel", response_model=MediaJobResponse, tags=["Media jobs"], summary="Cancel a pending media job")
# PUBLIC_INTERFACE
def cancel_media_job(
    job_id: str,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
) -> MediaJobResponse:
    """Cancel a queued or processing job before a safety result is finalized."""
    job = _get_job_or_404(session, job_id)
    if job.status not in {JobStatus.QUEUED.value, JobStatus.VALIDATING.value, JobStatus.PROCESSING.value}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only pending jobs can be cancelled.")
    job.status = JobStatus.CANCELLED.value
    job.completed_at = datetime.now(UTC)
    job.message = "Processing was cancelled before a safety result was finalized."
    record_actor_audit_event(session, "media_job.cancelled", "media_job", job.id, actor, "Pending processing cancelled.")
    session.commit()
    session.refresh(job)
    return _job_response(job)


@app.post(
    "/api/v1/media-jobs/{job_id}/approve-test-retention",
    response_model=MediaJobResponse,
    tags=["Media jobs"],
    summary="Approve extended retention for test media",
)
# PUBLIC_INTERFACE
def approve_test_media_retention(
    job_id: str,
    request: TestMediaRetentionApprovalRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR)),
) -> MediaJobResponse:
    """Extend a test-media job's private raw-media retention with explicit administrator approval.

    Only jobs uploaded with ``is_test_media=True`` are eligible: this workflow must never be
    used to extend retention for real operational/CCTV media, since that would undermine the
    fixed, non-identifying-by-default retention posture the POC otherwise guarantees.

    Args:
        job_id: The test-media job whose raw-media retention is being extended.
        request: The approved retention duration (hours) and a required justification note.

    Returns:
        The updated job record showing its new expiry and approval state.
    """
    job = _get_job_or_404(session, job_id)
    if not job.is_test_media:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only media explicitly flagged as test media at upload time is eligible for this workflow.",
        )
    job.test_retention_approved = True
    job.expires_at = datetime.now(UTC) + timedelta(hours=request.retention_hours)
    record_actor_audit_event(
        session,
        "media_job.test_retention_approved",
        "media_job",
        job.id,
        actor,
        f"Test-media retention extended to {request.retention_hours}h. Justification: {request.justification}",
    )
    session.commit()
    session.refresh(job)
    return _job_response(job)


@app.get("/api/v1/alerts", response_model=list[AlertResponse], tags=["Alerts"], summary="List safety alerts")
# PUBLIC_INTERFACE
def list_alerts(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(
        require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.DEMO_VIEWER)
    ),
) -> list[AlertResponse]:
    """Return persisted non-identifying safety alerts without raw media locations."""
    alerts = session.scalars(select(ComplianceAlert).order_by(ComplianceAlert.created_at.desc())).all()
    return [_alert_response(item) for item in alerts]


@app.post(
    "/api/v1/alerts/{alert_id}/acknowledgements",
    response_model=AlertResponse,
    tags=["Alerts"],
    summary="Acknowledge a safety alert",
)
# PUBLIC_INTERFACE
def acknowledge_alert(
    alert_id: str,
    request: AlertActionRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR)),
) -> AlertResponse:
    """Record a supervisor safety-intervention note against one open alert."""
    alert = _get_alert_or_404(session, alert_id)
    if alert.status != "open":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only open alerts may be acknowledged.")
    prior_status = alert.status
    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.now(UTC)
    alert.acknowledgement_note = request.note
    _record_acknowledgement(session, alert, actor, prior_status, request.note)
    record_actor_audit_event(session, "alert.acknowledged", "compliance_alert", alert.id, actor, "Safety intervention recorded.")
    session.commit()
    session.refresh(alert)
    return _alert_response(alert)


@app.post("/api/v1/alerts/{alert_id}/resolve", response_model=AlertResponse, tags=["Alerts"], summary="Resolve a safety alert")
# PUBLIC_INTERFACE
def resolve_alert(
    alert_id: str,
    request: AlertActionRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR)),
) -> AlertResponse:
    """Record a supervisor-reviewed resolution without inferring any personnel outcome."""
    alert = _get_alert_or_404(session, alert_id)
    if alert.status not in {"open", "acknowledged"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only active alerts may be resolved.")
    prior_status = alert.status
    alert.status = "resolved"
    alert.resolved_at = datetime.now(UTC)
    alert.resolution_note = request.note
    _record_acknowledgement(session, alert, actor, prior_status, request.note)
    record_actor_audit_event(session, "alert.resolved", "compliance_alert", alert.id, actor, "Supervisor-reviewed safety event resolved.")
    session.commit()
    session.refresh(alert)
    return _alert_response(alert)


@app.get(
    "/api/v1/alerts/{alert_id}/rule-results",
    response_model=list[RuleResultResponse],
    tags=["Alerts"],
    summary="Get the explainable rule results behind an alert",
)
# PUBLIC_INTERFACE
def list_alert_rule_results(
    alert_id: str,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER)),
) -> list[RuleResultResponse]:
    """Return every durable rule-evaluation record behind one alert, oldest first."""
    _get_alert_or_404(session, alert_id)
    results = session.scalars(
        select(EventRuleResult).where(EventRuleResult.alert_id == alert_id).order_by(EventRuleResult.created_at)
    ).all()
    return [
        RuleResultResponse(
            id=item.id,
            requirement=item.requirement,
            persistent=item.persistent,
            non_compliant_count=item.non_compliant_count,
            confidence=item.confidence,
            created_at=item.created_at,
        )
        for item in results
    ]


@app.get(
    "/api/v1/alerts/{alert_id}/acknowledgements",
    response_model=list[AcknowledgementResponse],
    tags=["Alerts"],
    summary="Get the full acknowledgement/resolution history for an alert",
)
# PUBLIC_INTERFACE
def list_alert_acknowledgements(
    alert_id: str,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER)),
) -> list[AcknowledgementResponse]:
    """Return every human-review action on one alert, oldest first -- not only the latest."""
    _get_alert_or_404(session, alert_id)
    results = session.scalars(
        select(EventAcknowledgement).where(EventAcknowledgement.alert_id == alert_id).order_by(EventAcknowledgement.created_at)
    ).all()
    return [
        AcknowledgementResponse(
            id=item.id,
            actor_role=item.actor_role,
            prior_status=item.prior_status,
            next_status=item.next_status,
            note=item.note,
            created_at=item.created_at,
        )
        for item in results
    ]


@app.post(
    "/api/v1/alerts/{alert_id}/evidence/approve-demo",
    response_model=AlertResponse,
    tags=["Alerts"],
    summary="Approve alert evidence for demonstration-viewer access",
)
# PUBLIC_INTERFACE
def approve_alert_evidence_for_demo(
    alert_id: str,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
) -> AlertResponse:
    """Mark one alert's current evidence as pre-approved for the demonstration-viewer role.

    The demonstration-viewer role never gets standing evidence access: each snapshot must be
    explicitly approved here first, and approval does not survive evidence expiry or a new
    snapshot being generated for the same alert.
    """
    alert = _get_alert_or_404(session, alert_id)
    snapshot = session.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.alert_id == alert.id))
    if _evidence_missing_or_expired(snapshot):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Privacy-processed evidence is unavailable or expired.")
    snapshot.demo_approved = True
    record_actor_audit_event(session, "evidence.demo_approved", "evidence_snapshot", snapshot.id, actor, "Evidence approved for demonstration-viewer access.")
    session.commit()
    session.refresh(alert)
    return _alert_response(alert)


@app.get(
    "/api/v1/alerts/{alert_id}/evidence",
    tags=["Alerts"],
    summary="Retrieve face-blurred alert evidence",
    responses={200: {"content": {"image/jpeg": {}}}, 404: {"description": "Evidence unavailable or expired"}},
)
# PUBLIC_INTERFACE
def get_alert_evidence(
    alert_id: str,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.SUPERVISOR, Role.DEMO_VIEWER)),
) -> Response:
    """Return only current, face-blurred evidence after separate reviewer authorization.

    A supervisor may view any current, unexpired evidence. The demonstration-viewer role may
    view only evidence an authorized reviewer has explicitly approved for demonstration use.

    Args:
        alert_id: Identifier of the reviewable non-compliance alert.

    Returns:
        A face-blurred JPEG only when it remains within the approved retention period.
    """
    alert = _get_alert_or_404(session, alert_id)
    snapshot = session.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.alert_id == alert.id))
    if _evidence_missing_or_expired(snapshot):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Privacy-processed evidence is unavailable or expired.")
    if actor.role is Role.DEMO_VIEWER and not snapshot.demo_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This evidence has not been approved for demonstration viewing.")
    try:
        from app.evidence import EvidenceService

        content = EvidenceService().read(snapshot.storage_key)
    except (FileNotFoundError, ImportError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Privacy-processed evidence is unavailable.") from error

    record_actor_audit_event(session, "evidence.accessed", "evidence_snapshot", snapshot.id, actor, "Face-blurred evidence viewed.")
    session.commit()
    return Response(content=content, media_type="image/jpeg")


@app.get("/api/v1/reports/compliance", response_model=ComplianceReport, tags=["Reports"], summary="Get aggregate compliance metrics")
# PUBLIC_INTERFACE
def compliance_report(
    zone_id: Annotated[str | None, Query(description="Optional configured zone identifier.")] = None,
    source_id: Annotated[str | None, Query(description="Optional configured source identifier.")] = None,
    shift: Annotated[str | None, Query(description="Optional configured shift label.")] = None,
    rule_key: Annotated[str | None, Query(description="Optional PPE rule label.")] = None,
    start_at: Annotated[datetime | None, Query(description="Inclusive report start timestamp.")] = None,
    end_at: Annotated[datetime | None, Query(description="Inclusive report end timestamp.")] = None,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(
        require_role(Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER, Role.DEMO_VIEWER)
    ),
) -> ComplianceReport:
    """Return scoped aggregate non-identifying compliance metrics for the dashboard."""
    filters = _report_filters(zone_id, source_id, shift, rule_key, start_at, end_at)
    reporting = AggregateReportingService()
    totals = reporting.compliance_totals(session, filters)
    open_alerts = reporting.alert_totals(session, filters)["states"]["open"]
    return ComplianceReport(
        **totals,
        open_alerts=open_alerts,
        disclaimer="POC-only aggregate safety indicators. Unknown observations are excluded from the compliance-rate denominator.",
    )


@app.get("/api/v1/reports/compliance/trend", response_model=list[TrendPoint], tags=["Reports"], summary="Get aggregate compliance trend")
# PUBLIC_INTERFACE
def compliance_trend(
    zone_id: str | None = None,
    source_id: str | None = None,
    shift: str | None = None,
    rule_key: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER, Role.DEMO_VIEWER)),
) -> list[TrendPoint]:
    """Return daily aggregate trend points with observed and unknown counts."""
    return [TrendPoint(**row) for row in AggregateReportingService().daily_trend(session, _report_filters(zone_id, source_id, shift, rule_key, start_at, end_at))]


@app.get("/api/v1/reports/alerts", response_model=AlertMetricsReport, tags=["Reports"], summary="Get aggregate alert metrics")
# PUBLIC_INTERFACE
def alert_metrics_report(
    zone_id: str | None = None,
    source_id: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER, Role.DEMO_VIEWER)),
) -> AlertMetricsReport:
    """Return scoped lifecycle counts and human-review timing metrics without evidence."""
    metrics = AggregateReportingService().alert_totals(session, _report_filters(zone_id, source_id, None, None, start_at, end_at))
    return AlertMetricsReport(**metrics, disclaimer="Aggregate POC alert metrics only. Alerts remain human-review safety signals.")


@app.post("/api/v1/reports/exports", tags=["Reports"], summary="Export aggregate compliance data")
# PUBLIC_INTERFACE
def export_compliance_report(
    request: AggregateExportRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER)),
) -> Response:
    """Create an audited CSV containing non-identifying aggregate compliance rows only."""
    rows = AggregateReportingService().export_rows(
        session,
        _report_filters(request.zone_id, request.source_id, request.shift, request.rule_key, request.start_at, request.end_at),
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["date", "zone_id", "source_id", "shift", "rule_key", "compliant", "non_compliant", "unknown"])
    writer.writeheader()
    writer.writerows(rows)
    record_actor_audit_event(session, "report.exported", "aggregate_report", "compliance", actor, "Aggregate-only CSV export created.")
    session.commit()
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aggregate-compliance-report.csv"},
    )


@app.get("/api/v1/model-evaluations", response_model=list[ModelEvaluationResponse], tags=["Model evaluation"], summary="List model evaluations")
# PUBLIC_INTERFACE
def list_model_evaluations(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.MODEL_EVALUATOR, Role.ADMINISTRATOR)),
) -> list[ModelEvaluationResponse]:
    """Return class-level benchmark outputs without presenting a single headline accuracy."""
    evaluations = session.scalars(select(ModelEvaluation).order_by(ModelEvaluation.created_at.desc())).all()
    return [_model_evaluation_response(item) for item in evaluations]


@app.post("/api/v1/model-evaluations", response_model=ModelEvaluationResponse, status_code=status.HTTP_201_CREATED, tags=["Model evaluation"], summary="Record a model evaluation")
# PUBLIC_INTERFACE
def create_model_evaluation(
    request: ModelEvaluationRequest,
    session: Session = Depends(get_session),
    actor: AuthenticatedActor = Depends(require_role(Role.MODEL_EVALUATOR)),
) -> ModelEvaluationResponse:
    """Record approved POC evaluation metadata, class metrics, licensing state, and limitations."""
    evaluation = ModelEvaluation(**request.model_dump(exclude={"class_metrics"}), class_metrics_json=json.dumps(request.class_metrics, sort_keys=True))
    session.add(evaluation)
    session.flush()
    record_actor_audit_event(session, "model_evaluation.created", "model_evaluation", evaluation.id, actor, "Class-level POC benchmark metadata recorded.")
    session.commit()
    session.refresh(evaluation)
    return _model_evaluation_response(evaluation)


@app.get("/api/v1/audit-events", response_model=list[AuditEventResponse], tags=["Audit"], summary="Review restricted audit records")
# PUBLIC_INTERFACE
def list_audit_events(
    session: Session = Depends(get_session),
    _: AuthenticatedActor = Depends(require_role(Role.ADMINISTRATOR, Role.GOVERNANCE_REVIEWER)),
) -> list[AuditEventResponse]:
    """Return recent restricted audit records without raw media, direct evidence links, or actor identities."""
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)).all()
    return [
        AuditEventResponse(
            id=event.id,
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            actor_role=event.actor_role,
            detail=event.detail,
            created_at=event.created_at,
        )
        for event in events
    ]


def _get_job_or_404(session: Session, job_id: str) -> MediaJob:
    """Load a media job or return a safe not-found response."""
    job = session.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media job not found.")
    return job


def _get_alert_or_404(session: Session, alert_id: str) -> ComplianceAlert:
    """Load a safety alert or return a safe not-found response."""
    alert = session.get(ComplianceAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Safety alert not found.")
    return alert


def _policy_response(policy: ZonePolicy) -> PolicyResponse:
    """Convert a persisted immutable policy version to its safe API representation."""
    return PolicyResponse(
        version=policy.version,
        helmet_required=policy.helmet_required,
        vest_required=policy.vest_required,
        confidence_threshold=policy.confidence_threshold,
        class_confidence_thresholds=json.loads(policy.class_confidence_thresholds_json or "{}"),
        persistence_frames=policy.persistence_frames,
        deduplication_seconds=policy.deduplication_seconds,
        sampling_fps=policy.sampling_fps,
        effective_start=policy.effective_start,
        effective_end=policy.effective_end,
    )


def _zone_response(zone: Zone, policy: ZonePolicy) -> ZoneResponse:
    """Convert a safety zone and its active policy to the public configuration shape."""
    return ZoneResponse(id=zone.id, name=zone.name, description=zone.description, policy=_policy_response(policy))


def _evidence_missing_or_expired(snapshot: EvidenceSnapshot | None) -> bool:
    """Return whether evidence is absent, unblurred, deleted, or past its retention expiry.

    Comparison is tolerant of SQLite's `DateTime` round-trip: a timezone-aware value the
    application writes comes back without tzinfo on read, even though it is always logically
    UTC. Treating a naive `expires_at` as UTC keeps this correct on SQLite and PostgreSQL alike.
    """
    if snapshot is None or not snapshot.blurred or snapshot.deleted_at is not None:
        return True
    expires_at = snapshot.expires_at if snapshot.expires_at.tzinfo else snapshot.expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _user_response(user: ApplicationUser) -> ApplicationUserResponse:
    """Convert a persisted role assignment to its administrator-facing representation."""
    return ApplicationUserResponse(
        id=user.id, auth_subject=user.auth_subject, role=user.role, enabled=user.enabled, created_at=user.created_at
    )


def _retention_settings_response(config: PlatformSettings) -> RetentionSettingsResponse:
    """Convert the singleton settings row to its retention-facing representation."""
    return RetentionSettingsResponse(
        raw_media_retention_hours=config.raw_media_retention_hours,
        frame_observation_retention_hours=config.frame_observation_retention_hours,
        updated_at=config.updated_at,
    )


def _inference_settings_response(config: PlatformSettings) -> InferenceSettingsResponse:
    """Convert the singleton settings row to its inference-facing representation."""
    return InferenceSettingsResponse(
        detection_provider=config.detection_provider,
        demo_mode=config.demo_mode,
        hf_model_repository=config.hf_model_repository,
        hf_model_filename=config.hf_model_filename,
        local_model_path=config.local_model_path,
        detection_confidence_threshold=config.detection_confidence_threshold,
        updated_at=config.updated_at,
    )


def _job_response(job: MediaJob) -> MediaJobResponse:
    """Convert a private persistence entity to its safe API representation."""
    return MediaJobResponse(
        id=job.id,
        source_id=job.source_id,
        zone_id=job.zone_id,
        filename=job.filename,
        status=job.status,
        submitted_at=job.submitted_at,
        completed_at=job.completed_at,
        compliant_count=job.compliant_count,
        non_compliant_count=job.non_compliant_count,
        unknown_count=job.unknown_count,
        message=job.message,
        failure_code=job.failure_code,
        is_test_media=job.is_test_media,
        test_retention_approved=job.test_retention_approved,
    )


def _record_acknowledgement(
    session: Session,
    alert: ComplianceAlert,
    actor: AuthenticatedActor,
    prior_status: str,
    note: str,
) -> None:
    """Append one human-review action to the alert's full acknowledgement history.

    Distinct from the audit log and from the latest-note fields still kept on the alert
    itself for quick display: this is the durable, queryable "timestamp, operator
    reference, prior state, next state, note" record for every action, not only the most
    recent one.
    """
    session.add(
        EventAcknowledgement(
            alert_id=alert.id,
            actor_reference=actor.reference,
            actor_role=actor.role.value,
            prior_status=prior_status,
            next_status=alert.status,
            note=note,
        )
    )


def _alert_response(alert: ComplianceAlert) -> AlertResponse:
    """Convert a persisted alert to its safe non-identifying API representation."""
    return AlertResponse(
        id=alert.id,
        source_id=alert.source_id,
        zone_id=alert.zone_id,
        status=alert.status,
        failed_requirement=alert.failed_requirement,
        confidence=alert.confidence,
        occurrence_count=alert.occurrence_count,
        created_at=alert.created_at,
        acknowledged_at=alert.acknowledged_at,
        acknowledgement_note=alert.acknowledgement_note,
        resolved_at=alert.resolved_at,
        resolution_note=alert.resolution_note,
        evidence_available=alert.evidence_available,
        evidence_message=alert.evidence_message,
    )


def _report_filters(
    zone_id: str | None,
    source_id: str | None,
    shift: str | None,
    rule_key: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> ReportFilters:
    """Build validated aggregate report constraints and reject inverted date windows."""
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The report start time must not be after the end time.")
    return ReportFilters(zone_id, source_id, shift, rule_key, start_at, end_at)


def _model_evaluation_response(evaluation: ModelEvaluation) -> ModelEvaluationResponse:
    """Convert persisted evaluation data into transparent class-level API output."""
    return ModelEvaluationResponse(
        id=evaluation.id,
        provider=evaluation.provider,
        model_name=evaluation.model_name,
        model_version=evaluation.model_version,
        dataset_reference=evaluation.dataset_reference,
        licence_status=evaluation.licence_status,
        approval_state=evaluation.approval_state,
        class_metrics=json.loads(evaluation.class_metrics_json),
        latency_ms=evaluation.latency_ms,
        effective_sampling_rate=evaluation.effective_sampling_rate,
        limitations=evaluation.limitations,
        created_at=evaluation.created_at,
    )
