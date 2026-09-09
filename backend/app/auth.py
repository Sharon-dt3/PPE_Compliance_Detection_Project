"""Authentication and server-side authorization for the PPE safety POC."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Annotated, Callable

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.models import ApplicationUser


class Role(str, Enum):
    """Roles used for safety workflows and privacy-governance controls."""

    SUPERVISOR = "safety_supervisor"
    HSE_MANAGER = "hse_manager"
    ADMINISTRATOR = "administrator"
    MODEL_EVALUATOR = "model_evaluator"
    GOVERNANCE_REVIEWER = "governance_reviewer"
    DEMO_VIEWER = "demonstration_viewer"


@dataclass(frozen=True)
class AuthenticatedActor:
    """Verified caller identity and server-side authorization role."""

    reference: str
    role: Role


@lru_cache(maxsize=1)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    """Build and cache one JWKS client per configured endpoint for signing-key lookups."""
    return jwt.PyJWKClient(jwks_url)


def _decode_supabase_jwt(token: str) -> dict[str, object]:
    """Verify a bearer token's signature against the identity provider's published JWKS.

    Verification never relies on a shared secret: the token's ``kid`` header selects the
    matching public key from the provider's JWKS endpoint, so the backend only ever holds
    public material and key rotation on the provider side requires no redeployment here.
    """
    jwks_url = settings.resolve_jwks_url()
    if not jwks_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured.")

    algorithms = [value.strip() for value in settings.supabase_jwt_algorithms.split(",") if value.strip()]
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=algorithms,
            audience=settings.supabase_jwt_audience,
        )
    except jwt.PyJWKClientError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured.") from error
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The access token is invalid or expired.") from error


def current_actor(
    authorization: Annotated[str | None, Header()] = None,
    x_demo_role: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> AuthenticatedActor:
    """Authenticate a caller and resolve their enabled server-side role assignment."""
    if settings.auth_mode == "demo":
        try:
            return AuthenticatedActor(reference="demo-session", role=Role(x_demo_role or Role.SUPERVISOR.value))
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported POC role.") from error

    if settings.auth_mode != "supabase":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A bearer token is required.")

    token = authorization.removeprefix("Bearer ").strip()
    claims = _decode_supabase_jwt(token)

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The access token does not identify a user.")
    user = session.query(ApplicationUser).filter_by(auth_subject=subject, enabled=True).one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No enabled application role is assigned.")
    try:
        return AuthenticatedActor(reference=user.auth_subject, role=Role(user.role))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The assigned application role is invalid.") from error


def require_role(*roles: Role) -> Callable[[AuthenticatedActor], AuthenticatedActor]:
    """Create a dependency that admits only callers assigned one of the listed roles."""

    def checker(actor: AuthenticatedActor = Depends(current_actor)) -> AuthenticatedActor:
        """Reject a request when its verified server-side role is not permitted."""
        if actor.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not permitted to perform this action.")
        return actor

    return checker
