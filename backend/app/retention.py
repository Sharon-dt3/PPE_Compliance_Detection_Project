"""Retention cleanup for private raw media and privacy-processed evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models import EvidenceSnapshot, MediaJob
from app.evidence import EvidenceService
from app.storage import PrivateMediaStorage


def remove_expired_private_data() -> None:
    """Delete expired raw media and evidence while retaining only allowed aggregate records."""
    now = datetime.now(UTC)
    media_storage = PrivateMediaStorage()
    evidence_storage = EvidenceService()

    with SessionLocal() as session:
        expired_jobs = session.scalars(
            select(MediaJob).where(MediaJob.expires_at <= now, MediaJob.storage_key.is_not(None))
        ).all()
        for job in expired_jobs:
            media_storage.delete(job.storage_key)
            job.storage_key = f"expired-{job.id}"

        expired_evidence = session.scalars(
            select(EvidenceSnapshot).where(EvidenceSnapshot.expires_at <= now, EvidenceSnapshot.deleted_at.is_(None))
        ).all()
        for snapshot in expired_evidence:
            evidence_storage.delete(snapshot.storage_key)
            snapshot.deleted_at = now

        session.commit()
