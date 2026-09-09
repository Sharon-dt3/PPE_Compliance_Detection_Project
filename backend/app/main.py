"""FastAPI entry point for the privacy-first PPE Compliance Detection POC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session, initialise_database
from app.evidence import EvidenceService
from app.models import CameraSource, ComplianceAlert, EvidenceSnapshot, JobStatus, MediaJob, MetricRollup, Zone, ZonePolicy
from app.storage import PrivateMediaStorage
from app.worker import process_media_job_task


class Role(str, Enum):
    """POC roles supplied by the development-only request header."""

    SUPERVISOR = "safety_supervisor"
    HSE_MANAGER = "hse_manager"
    ADMINISTRATOR = "administrator"
    DEMO_VIEWER = "demonstration_viewer"


class PolicyResponse(BaseModel):
    """Public representation of one active zone policy."""

    version: int
    helmet_required: bool
    vest_required: bool
    persistence_frames: int
    deduplication_seconds: int


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


class AlertAcknowledgementRequest(BaseModel):
    """Supervisor note captured when acknowledging a reviewable safety alert."""

    note: str = Field(min_length=3, max_length=500, description="Safety intervention note.")


class AlertResponse(BaseModel):
    """Non-identifying alert information available to authorized reviewers."""

    id: str
    source_id: str
    zone_id: str
    status: str
    failed_requirement: str
    confidence: float
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledgement_note: str | None
    evidence_available: bool
    evidence_message: str


class ComplianceReport(BaseModel):
    """Aggregate dashboard values that intentionally exclude worker identities."""

    observed: int
    compliant: int
    non_compliant: int
    unknown: int
    compliance_rate: float | None
    open_alerts: int
    disclaimer: str


def actor_role(x_demo_role: Annotated[str | None, Header()] = None) -> Role:
    """Resolve the temporary POC role header; production OIDC replaces this dependency."""
    try:
        return Role(x_demo_role or Role.SUPERVISOR.value)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported POC role.") from error


def require_role(*roles: Role):
    """Create an authorization dependency for routes sharing permitted roles."""

    def checker(role: Role = Depends(actor_role)) -> Role:
        """Deny access when the request's POC role is not permitted."""
        if role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not permitted to perform this action.")
        return role

    return checker


def job_response(job: MediaJob) -> MediaJobResponse:
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
    )


def alert_response(alert: ComplianceAlert) -> AlertResponse:
    """Convert a persisted alert to its safe non-identifying API representation."""
    return AlertResponse(
        id=alert.id,
        source_id=alert.source_id,
        zone_id=alert.zone_id,
        status=alert.status,
        failed_requirement=alert.failed_requirement,
        confidence=alert.confidence,
        created_at=alert.created_at,
        acknowledged_at=alert.acknowledged_at,
        acknowledgement_note=alert.acknowledgement_note,
        evidence_available=alert.evidence_available,
        evidence_message=alert.evidence_message,
    )


app = FastAPI(
    title=settings.app_name,
    description="Safety-first POC API. Outputs are indicative, non-identifying decision-support signals, not automated enforcement.",
    version="0.3.0",
    openapi_tags=[
        {"name": "Health", "description": "Service health and readiness."},
        {"name": "Configuration", "description": "Configured safety zones and sources."},
        {"name": "Media jobs", "description": "Private manual-upload media processing."},
        {"name": "Alerts", "description": "Reviewable, non-identifying safety alerts and privacy-gated evidence."},
        {"name": "Reports", "description": "Aggregate safety metrics only."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Demo-Role"],
)


@app.on_event("startup")
async def startup() -> None:
    """Initialise the local schema and fixed POC configuration before accepting requests."""
    initialise_database()


@app.get("/health", tags=["Health"], summary="Check service health")
# PUBLIC_INTERFACE
def health_check() -> dict[str, str]:
    """Return service health for local development and deployment checks."""
    return {"status": "healthy"}


@app.get("/api/v1/zones", response_model=list[ZoneResponse], tags=["Configuration"], summary="List safety zones")
# PUBLIC_INTERFACE
def list_zones(
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.DEMO_VIEWER)),
) -> list[ZoneResponse]:
    """Return configured zones and their active PPE policies to permitted POC users."""
    zones = session.scalars(select(Zone).where(Zone.enabled.is_(True)).order_by(Zone.name)).all()
    results = []
    for zone in zones:
        policy = session.scalar(select(ZonePolicy).where(ZonePolicy.zone_id == zone.id, ZonePolicy.active.is_(True)))
        if policy:
            results.append(
                ZoneResponse(
                    id=zone.id,
                    name=zone.name,
                    description=zone.description,
                    policy=PolicyResponse(
                        version=policy.version,
                        helmet_required=policy.helmet_required,
                        vest_required=policy.vest_required,
                        persistence_frames=policy.persistence_frames,
                        deduplication_seconds=policy.deduplication_seconds,
                    ),
                )
            )
    return results


@app.get("/api/v1/sources", response_model=list[SourceResponse], tags=["Configuration"], summary="List camera sources")
# PUBLIC_INTERFACE
def list_sources(
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> list[SourceResponse]:
    """Return enabled manually selectable sources for private POC media submission."""
    sources = session.scalars(select(CameraSource).where(CameraSource.enabled.is_(True)).order_by(CameraSource.name)).all()
    return [SourceResponse(id=item.id, name=item.name, zone_id=item.zone_id) for item in sources]


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
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> MediaJobResponse:
    """Validate and persist a manual upload, then dispatch private processing through the worker.

    Args:
        source_id: Enabled source whose zone selects the active safety policy.
        file: Uploaded CCTV still or recorded video clip.

    Returns:
        A queued non-identifying job record. Private storage references are never returned.
    """
    source = session.get(CameraSource, source_id)
    if source is None or not source.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configured camera source not found.")
    policy = session.scalar(select(ZonePolicy).where(ZonePolicy.zone_id == source.zone_id, ZonePolicy.active.is_(True)))
    if policy is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The selected source has no active safety policy.")

    allowed_types = {"image/jpeg", "image/png", "video/mp4", "video/quicktime"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Supported uploads are JPEG, PNG, MP4, and MOV.")
    content = await file.read(settings.max_upload_bytes + 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The uploaded file exceeds the configured size limit.")

    filename = file.filename or "unnamed-upload"
    storage_key = PrivateMediaStorage().save(content, filename)
    job = MediaJob(
        source_id=source.id,
        zone_id=source.zone_id,
        policy_id=policy.id,
        filename=filename,
        content_type=file.content_type,
        storage_key=storage_key,
        status=JobStatus.QUEUED.value,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.raw_media_retention_hours),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        process_media_job_task.delay(job.id)
    except Exception:
        job.message = "Queued safely. The worker is currently unavailable; processing will resume when it is restored."
        session.commit()
    return job_response(job)


@app.get("/api/v1/media-jobs", response_model=list[MediaJobResponse], tags=["Media jobs"], summary="List media processing jobs")
# PUBLIC_INTERFACE
def list_media_jobs(
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> list[MediaJobResponse]:
    """Return safe status summaries for recent private media-processing jobs."""
    jobs = session.scalars(select(MediaJob).order_by(MediaJob.submitted_at.desc()).limit(25)).all()
    return [job_response(item) for item in jobs]


@app.get("/api/v1/media-jobs/{job_id}", response_model=MediaJobResponse, tags=["Media jobs"], summary="Get media job status")
# PUBLIC_INTERFACE
def get_media_job(
    job_id: str,
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> MediaJobResponse:
    """Return a safe processing summary for one persisted media job."""
    job = session.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media job not found.")
    return job_response(job)


@app.get("/api/v1/alerts", response_model=list[AlertResponse], tags=["Alerts"], summary="List safety alerts")
# PUBLIC_INTERFACE
def list_alerts(
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR)),
) -> list[AlertResponse]:
    """Return persisted non-identifying safety alerts without raw media locations."""
    alerts = session.scalars(select(ComplianceAlert).order_by(ComplianceAlert.created_at.desc())).all()
    return [alert_response(item) for item in alerts]


@app.post("/api/v1/alerts/{alert_id}/acknowledgements", response_model=AlertResponse, tags=["Alerts"], summary="Acknowledge a safety alert")
# PUBLIC_INTERFACE
def acknowledge_alert(
    alert_id: str,
    request: AlertAcknowledgementRequest,
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR)),
) -> AlertResponse:
    """Record a supervisor safety-intervention note against one open alert."""
    alert = session.get(ComplianceAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Safety alert not found.")
    if alert.status != "open":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only open alerts may be acknowledged.")
    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.now(UTC)
    alert.acknowledgement_note = request.note
    session.commit()
    session.refresh(alert)
    return alert_response(alert)


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
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
) -> Response:
    """Return only currently available, privacy-processed evidence for an authorized reviewer.

    Args:
        alert_id: Identifier of the reviewable non-compliance alert.

    Returns:
        JPEG evidence after face-blur validation, or a safe unavailable response.
    """
    alert = session.get(ComplianceAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Safety alert not found.")
    snapshot = session.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.alert_id == alert.id))
    if (
        snapshot is None
        or not snapshot.blurred
        or snapshot.deleted_at is not None
        or snapshot.expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Privacy-processed evidence is unavailable or expired.")
    try:
        return Response(content=EvidenceService().read(snapshot.storage_key), media_type="image/jpeg")
    except FileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Privacy-processed evidence is unavailable.") from error


@app.get("/api/v1/reports/compliance", response_model=ComplianceReport, tags=["Reports"], summary="Get aggregate compliance metrics")
# PUBLIC_INTERFACE
def compliance_report(
    session: Session = Depends(get_session),
    _: Role = Depends(require_role(Role.SUPERVISOR, Role.HSE_MANAGER, Role.ADMINISTRATOR, Role.DEMO_VIEWER)),
) -> ComplianceReport:
    """Return aggregate non-identifying compliance metrics for the dashboard."""
    totals = session.execute(
        select(
            func.coalesce(func.sum(MetricRollup.compliant), 0),
            func.coalesce(func.sum(MetricRollup.non_compliant), 0),
            func.coalesce(func.sum(MetricRollup.unknown), 0),
        )
    ).one()
    compliant, non_compliant, unknown = (int(value) for value in totals)
    observed = compliant + non_compliant
    open_alerts = session.scalar(select(func.count()).select_from(ComplianceAlert).where(ComplianceAlert.status == "open")) or 0
    return ComplianceReport(
        observed=observed,
        compliant=compliant,
        non_compliant=non_compliant,
        unknown=unknown,
        compliance_rate=round(compliant / observed * 100, 1) if observed else None,
        open_alerts=int(open_alerts),
        disclaimer="POC-only aggregate safety indicators. Unknown observations are excluded from the compliance-rate denominator.",
    )
