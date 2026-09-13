"""Shared-deployment readiness checks for the configured database and Redis backends.

Configuration alone (``PPE_DATABASE_URL`` / ``PPE_REDIS_URL``) does not prove a shared
PostgreSQL or Redis deployment is actually reachable: a typo, missing driver, network rule,
or unprovisioned instance would otherwise only surface the first time a request needs it.
These checks open a real connection/ping against each configured backend so a misconfigured
shared deployment fails loudly at a known, easily monitored endpoint instead of silently
falling back to whatever local default happens to still be reachable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


@dataclass(frozen=True)
class InfraReadiness:
    """Structured result of a single infrastructure-dependency readiness check.

    ``status`` is one of ``"ready"`` (the backend responded successfully), or
    ``"unavailable"`` (the configured backend could not be reached or rejected the probe).
    There is no ``"not_configured"`` state here: unlike the optional face-detector model,
    a database and a message broker are load-bearing for every request, so an empty or
    invalid URL is itself treated as ``"unavailable"`` rather than an expected local state.
    """

    status: str
    detail: str


# PUBLIC_INTERFACE
def check_database_readiness() -> InfraReadiness:
    """Verify the configured database URL is reachable with a trivial round-trip query.

    Confirms the Phase 3 "shared PostgreSQL deployment" requirement independently of any
    request path: this opens one real connection through the same engine used by the
    application and executes ``SELECT 1``, so a missing driver, unreachable host, or
    invalid credential is reported here rather than surfacing only on the first real
    request. Never raises: unexpected failures are converted into a structured result so
    a health endpoint can report readiness without crashing the process.

    Returns:
        An ``InfraReadiness`` describing whether the configured database responded to a
        real connection attempt, and if not, a safe (credential-free) failure summary.
    """
    from app.database import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        return InfraReadiness(
            status="unavailable",
            detail=f"The configured database could not be reached: {type(error).__name__}.",
        )
    return InfraReadiness(
        status="ready",
        detail=f"The configured database responded successfully ({engine.dialect.name} dialect).",
    )


# PUBLIC_INTERFACE
def check_redis_readiness() -> InfraReadiness:
    """Verify the configured Redis URL (Celery broker/backend) responds to a PING.

    Confirms the Phase 3 "shared Redis deployment" requirement independently of any queued
    job: an unreachable or misconfigured Redis instance would otherwise only be discovered
    when a media upload silently fails to dispatch for background processing. Never raises:
    connection and protocol errors are converted into a structured result.

    Returns:
        An ``InfraReadiness`` describing whether the configured Redis instance responded to
        a PING, and if not, a safe (credential-free) failure summary.
    """
    import redis as redis_client
    from app.config import settings

    try:
        client = redis_client.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
        try:
            client.ping()
        finally:
            client.close()
    except redis_client.RedisError as error:
        return InfraReadiness(
            status="unavailable",
            detail=f"The configured Redis instance could not be reached: {type(error).__name__}.",
        )
    return InfraReadiness(status="ready", detail="The configured Redis instance responded successfully to PING.")
