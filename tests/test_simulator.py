from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient

from app.channels.manager import channel_registry
from app.channels.whatsapp import WhatsAppAdapter
from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User

pytestmark = pytest.mark.asyncio

SIMULATOR_URL = "/api/v1/simulator/send"


async def create_user_with_role(db: AsyncSession, role: str) -> tuple[User, str]:
    user = User(
        email=f"sim_user_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password123"),
        full_name="Sim User",
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    token = create_access_token(subject=user.id, role=user.role)
    return user, token


class TestSimulatorEndpoint:
    async def test_simulator_disabled_returns_403(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """When ENABLE_SIMULATOR is False, request is rejected with 403."""
        monkeypatch.setattr(settings, "ENABLE_SIMULATOR", False)
        admin_user, admin_token = await create_user_with_role(db_session, "admin")

        resp = await client.post(
            SIMULATOR_URL,
            json={
                "channel": "whatsapp",
                "external_user_id": "16315559999",
                "text": "Hello simulator",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()

    async def test_non_admin_rejected_403(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Regular agent cannot access simulator endpoint."""
        monkeypatch.setattr(settings, "ENABLE_SIMULATOR", True)
        monkeypatch.setattr(settings, "DEMO_MODE", False)
        agent_user, agent_token = await create_user_with_role(db_session, "agent")

        resp = await client.post(
            SIMULATOR_URL,
            json={
                "channel": "whatsapp",
                "external_user_id": "16315559999",
                "text": "Hello agent",
            },
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 403

    async def test_agent_can_use_simulator_in_demo_mode(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENABLE_SIMULATOR", True)
        monkeypatch.setattr(settings, "DEMO_MODE", True)
        _, agent_token = await create_user_with_role(db_session, "agent")

        resp = await client.post(
            SIMULATOR_URL,
            json={
                "channel": "whatsapp",
                "external_user_id": f"demo_agent_{uuid.uuid4().hex[:8]}",
                "text": "Hello from the demo agent",
            },
            headers={"Authorization": "Bearer " + agent_token},
        )

        assert resp.status_code == 200

    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger", "telegram"])
    async def test_simulator_end_to_end_flow(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
        channel: str,
    ):
        """Simulated message creates conversation, stores inbound message, and dispatches background AI reply."""
        monkeypatch.setattr(settings, "ENABLE_SIMULATOR", True)
        admin_user, admin_token = await create_user_with_role(db_session, "admin")

        ext_uid = f"sim_user_{channel}_{uuid.uuid4().hex[:6]}"
        resp = await client.post(
            SIMULATOR_URL,
            json={
                "channel": channel,
                "external_user_id": ext_uid,
                "customer_name": f"Simulated {channel.capitalize()} User",
                "text": f"Simulated inquiry on {channel}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "delivered"
        assert data["channel"] == channel
        assert data["external_user_id"] == ext_uid

        # Verify conversation in DB
        stmt = select(Conversation).where(
            Conversation.channel == channel,
            Conversation.external_user_id == ext_uid,
        )
        res = await db_session.execute(stmt)
        conv = res.scalar_one_or_none()
        assert conv is not None
        assert conv.status == "open"
        assert conv.unread_count == 1

        # Verify inbound message in DB
        stmt_msg = select(Message).where(Message.conversation_id == conv.id)
        res_msg = await db_session.execute(stmt_msg)
        messages = res_msg.scalars().all()
        assert len(messages) >= 1
        inbound = next(m for m in messages if m.direction == "inbound")
        assert inbound.text == f"Simulated inquiry on {channel}"
