from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_password
from app.models.user import User
from scripts.seed import seed

pytestmark = pytest.mark.asyncio


class TestDemoInfo:
    async def test_demo_info_hides_password_when_demo_mode_is_disabled(
        self, client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(settings, "DEMO_MODE", False)

        response = await client.get("/api/v1/demo-info")

        assert response.status_code == 200
        assert response.json() == {"demo": False, "email": ""}
        assert "password" not in response.json()

    async def test_demo_info_includes_demo_credentials_when_enabled(
        self, client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(settings, "DEMO_MODE", True)
        monkeypatch.setattr(settings, "DEMO_PASSWORD", "test-demo-password")

        response = await client.get("/api/v1/demo-info")

        assert response.status_code == 200
        assert response.json() == {
            "demo": True,
            "email": "demo@example.com",
            "password": "test-demo-password",
        }

    async def test_seed_creates_agent_demo_account(
        self, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "DEMO_MODE", True)
        monkeypatch.setattr(settings, "DEMO_PASSWORD", "test-demo-password")

        await seed()

        result = await db_session.execute(
            select(User).where(User.email == "demo@example.com")
        )
        demo_user = result.scalar_one()
        assert demo_user.role == "agent"
        assert demo_user.is_active is True
        assert verify_password("test-demo-password", demo_user.hashed_password)

    async def test_ui_and_existing_routes_are_served(self, client: AsyncClient):
        root = await client.get("/")
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")
        health = await client.get("/health")

        assert root.status_code == 200
        assert "<title>OmniMessage Inbox</title>" in root.text
        assert docs.status_code == 200
        assert openapi.status_code == 200
        assert health.json() == {"status": "ok"}
