"""Append-only, non-identifying operational audit records."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth import AuthenticatedActor
from app.models import AuditEvent


def record_audit_event(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_reference: str,
    actor_role: str,
    detail: str,
) -> None:
    """Append a restricted audit entry in the active database transaction.

    Audit entries intentionally contain no raw media location, face data, personnel
    profile, or client-supplied role claim. The caller supplies an authenticated actor
    reference or a named system actor.
    """
    session.add(
        AuditEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_reference=actor_reference,
            actor_role=actor_role,
            detail=detail,
        )
    )


def record_actor_audit_event(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: AuthenticatedActor,
    detail: str,
) -> None:
    """Append an audit entry for a previously authenticated application actor."""
    record_audit_event(
        session=session,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_reference=actor.reference,
        actor_role=actor.role.value,
        detail=detail,
    )
