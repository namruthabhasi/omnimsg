from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenResponse, UserResponse


class AuthService:
    """Handles registration, login, and token refresh / revocation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Registration ──────────────────────────────────────────

    async def register(self, payload: RegisterRequest) -> UserResponse:
        # Check for existing user
        stmt = select(User).where(User.email == payload.email)
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            raise ValueError("A user with this email already exists")

        user = User(
            email=payload.email,
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            role=payload.role,
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return UserResponse.model_validate(user)

    # ── Login ─────────────────────────────────────────────────

    async def login(self, email: str, password: str) -> TokenResponse:
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.hashed_password):
            raise ValueError("Invalid email or password")
        if not user.is_active:
            raise ValueError("Account is deactivated")

        return await self._issue_tokens(user)

    # ── Refresh ───────────────────────────────────────────────

    async def refresh(self, refresh_token_str: str) -> TokenResponse:
        try:
            payload = decode_token(refresh_token_str)
        except Exception:
            raise ValueError("Invalid or expired refresh token")

        if payload.get("type") != "refresh":
            raise ValueError("Token is not a refresh token")

        jti = payload.get("jti")
        if not jti:
            raise ValueError("Invalid refresh token")

        # Look up the stored refresh token
        stmt = select(RefreshToken).where(RefreshToken.jti == jti)
        result = await self.db.execute(stmt)
        stored = result.scalar_one_or_none()

        if not stored:
            raise ValueError("Refresh token not found")
        if stored.revoked_at is not None:
            raise ValueError("Refresh token has been revoked")
        if stored.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise ValueError("Refresh token has expired")

        # Revoke the old token (rotation)
        stored.revoked_at = datetime.now(timezone.utc)

        # Fetch the user
        user_stmt = select(User).where(User.id == stored.user_id)
        user_result = await self.db.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        if not user or not user.is_active:
            raise ValueError("User not found or deactivated")

        return await self._issue_tokens(user)

    # ── Get user by ID ────────────────────────────────────────

    async def get_user_by_id(self, user_id: str) -> User | None:
        stmt = select(User).where(User.id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ── Helpers ───────────────────────────────────────────────

    async def _issue_tokens(self, user: User) -> TokenResponse:
        access_token = create_access_token(subject=user.id, role=user.role)
        refresh_token_str, jti, expires_at = create_refresh_token(subject=user.id)

        rt = RefreshToken(
            jti=jti,
            user_id=user.id,
            expires_at=expires_at,
        )
        self.db.add(rt)
        await self.db.flush()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_str,
        )
