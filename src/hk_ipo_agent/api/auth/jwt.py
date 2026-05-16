"""Local-JWT helpers per ADR 0011 §Phase 7 MVP.

PyJWT signs / verifies ``UserAccount`` claims. Real SSO providers
(OKTA / AzureAD / Google) deferred to Phase 9.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from ...common.exceptions import HkIpoAgentException
from ...common.settings import get_settings


class AuthError(HkIpoAgentException):
    """Raised on JWT validation failures."""


def issue_access_token(
    *,
    user_id: UUID,
    email: str,
    roles: list[str],
    ttl_seconds: int | None = None,
) -> tuple[str, int]:
    """Sign and return ``(token, expires_in_seconds)``."""
    settings = get_settings().auth
    ttl = ttl_seconds or settings.jwt_access_token_ttl_seconds
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": email,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, ttl


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify + decode an access token. Raises ``AuthError`` on invalid."""
    settings = get_settings().auth
    try:
        result: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
        return result
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc


def token_lifetime_remaining(payload: dict[str, Any]) -> int:
    """Return seconds left on a decoded token; <= 0 means expired."""
    exp = int(payload.get("exp", 0))
    return max(0, exp - int(time.time()))


__all__ = (
    "AuthError",
    "decode_access_token",
    "issue_access_token",
    "token_lifetime_remaining",
)
