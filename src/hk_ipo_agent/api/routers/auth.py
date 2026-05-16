"""Authentication endpoints — POST /api/auth/login (local JWT only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from ..auth import CurrentUserDep
from ..auth.dependencies import verify_user
from ..auth.jwt import issue_access_token
from ..schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    """Exchange email + password for an access token (local JWT)."""
    user = verify_user(payload.email, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
        )
    token, ttl = issue_access_token(
        user_id=user.id,
        email=user.email,
        roles=[r.value for r in user.roles],
    )
    return LoginResponse(
        access_token=token,
        expires_in_seconds=ttl,
        user_id=user.id,
        email=user.email,
        roles=user.roles,
    )


@router.get("/me")
async def me(user: CurrentUserDep) -> dict[str, Any]:
    """Return the current authenticated user's identity."""
    return {
        "user_id": str(user.id),
        "email": user.email,
        "roles": [r.value for r in user.roles],
    }


__all__ = ("router",)
