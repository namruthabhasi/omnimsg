from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
ME_URL = "/api/v1/auth/me"


# ── Helper ────────────────────────────────────────────────────

async def create_user(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "securepassword123",
    full_name: str = "Test User",
    role: str = "agent",
) -> dict:
    resp = await client.post(
        REGISTER_URL,
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "role": role,
        },
    )
    return resp.json()


async def login_user(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "securepassword123",
) -> dict:
    resp = await client.post(
        LOGIN_URL, json={"email": email, "password": password}
    )
    return resp.json()


# ── Registration Tests ────────────────────────────────────────


class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post(
            REGISTER_URL,
            json={
                "email": "new@example.com",
                "password": "strongpass123",
                "full_name": "New User",
                "role": "agent",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@example.com"
        assert data["full_name"] == "New User"
        assert data["role"] == "agent"
        assert data["is_active"] is True
        assert "id" in data

    async def test_register_duplicate_email(self, client: AsyncClient):
        await create_user(client, email="dup@example.com")
        resp = await client.post(
            REGISTER_URL,
            json={
                "email": "dup@example.com",
                "password": "strongpass123",
                "full_name": "Duplicate",
                "role": "agent",
            },
        )
        assert resp.status_code == 409

    async def test_register_invalid_email(self, client: AsyncClient):
        resp = await client.post(
            REGISTER_URL,
            json={
                "email": "not-an-email",
                "password": "strongpass123",
                "full_name": "Bad Email",
                "role": "agent",
            },
        )
        assert resp.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        resp = await client.post(
            REGISTER_URL,
            json={
                "email": "short@example.com",
                "password": "short",
                "full_name": "Short Pass",
                "role": "agent",
            },
        )
        assert resp.status_code == 422

    async def test_register_invalid_role(self, client: AsyncClient):
        resp = await client.post(
            REGISTER_URL,
            json={
                "email": "badrole@example.com",
                "password": "strongpass123",
                "full_name": "Bad Role",
                "role": "superadmin",
            },
        )
        assert resp.status_code == 422

    async def test_register_admin(self, client: AsyncClient):
        resp = await client.post(
            REGISTER_URL,
            json={
                "email": "admin@example.com",
                "password": "adminpass123",
                "full_name": "Admin User",
                "role": "admin",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"


# ── Login Tests ───────────────────────────────────────────────


class TestLogin:
    async def test_login_success(self, client: AsyncClient):
        await create_user(client)
        resp = await client.post(
            LOGIN_URL,
            json={"email": "test@example.com", "password": "securepassword123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client: AsyncClient):
        await create_user(client)
        resp = await client.post(
            LOGIN_URL,
            json={"email": "test@example.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post(
            LOGIN_URL,
            json={"email": "nobody@example.com", "password": "somepassword"},
        )
        assert resp.status_code == 401


# ── Token Refresh Tests ───────────────────────────────────────


class TestRefresh:
    async def test_refresh_success(self, client: AsyncClient):
        await create_user(client)
        tokens = await login_user(client)
        resp = await client.post(
            REFRESH_URL,
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New refresh token should be different (rotation)
        assert data["refresh_token"] != tokens["refresh_token"]

    async def test_refresh_revoked_token(self, client: AsyncClient):
        await create_user(client)
        tokens = await login_user(client)
        # Use refresh once (rotates)
        await client.post(
            REFRESH_URL,
            json={"refresh_token": tokens["refresh_token"]},
        )
        # Try to use the old (now revoked) refresh token again
        resp = await client.post(
            REFRESH_URL,
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 401

    async def test_refresh_invalid_token(self, client: AsyncClient):
        resp = await client.post(
            REFRESH_URL,
            json={"refresh_token": "invalid.token.here"},
        )
        assert resp.status_code == 401


# ── Protected Endpoint Tests ──────────────────────────────────


class TestCurrentUser:
    async def test_get_me_success(self, client: AsyncClient):
        await create_user(client)
        tokens = await login_user(client)
        resp = await client.get(
            ME_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "test@example.com"
        assert data["full_name"] == "Test User"

    async def test_get_me_no_token(self, client: AsyncClient):
        resp = await client.get(ME_URL)
        assert resp.status_code == 401

    async def test_get_me_invalid_token(self, client: AsyncClient):
        resp = await client.get(
            ME_URL,
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert resp.status_code == 401

    async def test_get_me_with_refresh_token_rejected(self, client: AsyncClient):
        """Using a refresh token on a protected endpoint should fail."""
        await create_user(client)
        tokens = await login_user(client)
        resp = await client.get(
            ME_URL,
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        assert resp.status_code == 401
