"""Tests for JWKS-verified authentication and server-side role resolution."""

from __future__ import annotations

from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import auth
from app.config import settings


def _rsa_keypair():
    """Build a throwaway RSA keypair so no real provider key material is needed."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key, **claims) -> str:
    """Sign a minimal Supabase-shaped access token with the given private key."""
    payload = {"sub": "subject-1", "aud": "authenticated", **claims}
    return jwt.encode(payload, private_key, algorithm="RS256")


class _StubSession:
    """Minimal session fake resolving a single fixed ApplicationUser row."""

    def __init__(self, user: object | None) -> None:
        self._user = user

    def query(self, _model: object) -> "_StubSession":
        """Return self so the fluent query interface can be chained."""
        return self

    def filter_by(self, **_kwargs: object) -> "_StubSession":
        """Ignore filter arguments and return self for chaining."""
        return self

    def one_or_none(self) -> object | None:
        """Return the fixed configured user row, or None when unassigned."""
        return self._user


def _configure_jwks(monkeypatch: pytest.MonkeyPatch, public_key: object) -> None:
    """Point auth at a fake JWKS resolver that always returns the given public key."""
    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_jwks_url", "")
    monkeypatch.setattr(settings, "supabase_jwt_algorithms", "RS256")
    monkeypatch.setattr(
        auth,
        "_jwks_client",
        lambda _url: SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)),
    )


def test_current_actor_verifies_signature_against_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token signed by the provider's own key resolves to its assigned server-side role."""
    private_key, public_key = _rsa_keypair()
    _configure_jwks(monkeypatch, public_key)
    user = SimpleNamespace(auth_subject="subject-1", role="administrator", enabled=True)

    actor = auth.current_actor(
        authorization=f"Bearer {_token(private_key)}", x_demo_role=None, session=_StubSession(user)
    )

    assert actor.reference == "subject-1"
    assert actor.role is auth.Role.ADMINISTRATOR


def test_current_actor_rejects_signature_from_an_unrelated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token signed by a different private key must never verify against the published JWKS."""
    _, public_key = _rsa_keypair()
    other_private_key, _ = _rsa_keypair()
    _configure_jwks(monkeypatch, public_key)

    with pytest.raises(HTTPException) as excinfo:
        auth.current_actor(
            authorization=f"Bearer {_token(other_private_key)}", x_demo_role=None, session=_StubSession(None)
        )

    assert excinfo.value.status_code == 401


def test_current_actor_rejects_a_verified_identity_with_no_role_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cryptographically valid token still requires a matching enabled ApplicationUser row."""
    private_key, public_key = _rsa_keypair()
    _configure_jwks(monkeypatch, public_key)

    with pytest.raises(HTTPException) as excinfo:
        auth.current_actor(
            authorization=f"Bearer {_token(private_key)}", x_demo_role=None, session=_StubSession(None)
        )

    assert excinfo.value.status_code == 403


def test_current_actor_fails_closed_without_a_configured_jwks_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supabase auth mode with no JWKS endpoint configured must never fall back to trusting a claim."""
    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_jwks_url", "")

    with pytest.raises(HTTPException) as excinfo:
        auth.current_actor(authorization="Bearer whatever-token", x_demo_role=None, session=_StubSession(None))

    assert excinfo.value.status_code == 503


def test_demo_mode_resolves_role_from_header_without_jwt_verification() -> None:
    """Local demo mode remains header-driven and never attempts JWKS verification."""
    actor = auth.current_actor(authorization=None, x_demo_role="hse_manager", session=_StubSession(None))

    assert actor.reference == "demo-session"
    assert actor.role is auth.Role.HSE_MANAGER
