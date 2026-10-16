from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import decrypt_value, encrypt_value
from app.core.security import create_access_token, hash_password
from app.models.channel_config import ChannelConfig
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message
from app.models.system_settings import SystemSettings
from app.models.user import User
from scripts.seed import seed

pytestmark = pytest.mark.asyncio

DASHBOARD_URL = "/api/v1/dashboard/stats"
CHANNELS_SETTINGS_URL = "/api/v1/settings/channels"
SYSTEM_SETTINGS_URL = "/api/v1/settings/system"
KB_URL = "/api/v1/knowledge-base"


@pytest.fixture(autouse=True)
def ensure_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key)


async def create_user(db: AsyncSession, role: str) -> tuple[User, str]:
    user = User(
        email=f"{role}_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password123"),
        full_name=f"{role.capitalize()} User",
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    token = create_access_token(subject=user.id, role=user.role)
    return user, token


class TestDashboardStats:
    async def test_empty_database_returns_zero_stats_without_error(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Empty database handles all aggregate calculations returning 0s and None for avg response time."""
        _, token = await create_user(db_session, "agent")
        resp = await client.get(DASHBOARD_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_conversations"] == 0
        assert data["status_counts"]["open"] == 0
        assert data["status_counts"]["needs_human"] == 0
        assert data["status_counts"]["closed"] == 0
        assert data["total_messages"] == 0
        assert data["ai_replies"] == 0
        assert data["human_replies"] == 0
        assert data["average_response_time_seconds"] is None

    async def test_response_time_and_metrics_calculation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Calculates precise average response time and reply ratios for known message timestamps."""
        _, token = await create_user(db_session, "agent")
        headers = {"Authorization": f"Bearer {token}"}

        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Conversation 1 (WhatsApp, response delta = 10 seconds, AI reply)
        c1 = Conversation(
            channel="whatsapp",
            external_user_id="wa_stats_1",
            status="open",
            created_at=t0,
        )
        # Conversation 2 (Telegram, response delta = 30 seconds, Human reply)
        c2 = Conversation(
            channel="telegram",
            external_user_id="tg_stats_2",
            status="needs_human",
            created_at=t0,
        )
        db_session.add_all([c1, c2])
        await db_session.flush()

        # Messages for c1
        m1_in = Message(
            conversation_id=c1.id,
            channel="whatsapp",
            external_message_id="wa_in_1",
            direction="inbound",
            sender_type="customer",
            text="Inquiry 1",
            timestamp=t0,
        )
        m1_out = Message(
            conversation_id=c1.id,
            channel="whatsapp",
            external_message_id="wa_out_1",
            direction="outbound",
            sender_type="ai",
            text="AI reply",
            timestamp=t0 + timedelta(seconds=10),
        )

        # Messages for c2
        m2_in = Message(
            conversation_id=c2.id,
            channel="telegram",
            external_message_id="tg_in_2",
            direction="inbound",
            sender_type="customer",
            text="Inquiry 2",
            timestamp=t0,
        )
        m2_out = Message(
            conversation_id=c2.id,
            channel="telegram",
            external_message_id="tg_out_2",
            direction="outbound",
            sender_type="human",
            text="Human reply",
            timestamp=t0 + timedelta(seconds=30),
        )

        db_session.add_all([m1_in, m1_out, m2_in, m2_out])
        await db_session.commit()

        resp = await client.get(DASHBOARD_URL, headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_conversations"] == 2
        assert data["status_counts"]["open"] == 1
        assert data["status_counts"]["needs_human"] == 1
        assert data["needs_human_count"] == 1

        # Response time: (10 + 30) / 2 = 20.0 seconds
        assert data["average_response_time_seconds"] == 20.0

        # AI vs Human reply ratio
        assert data["ai_replies"] == 1
        assert data["human_replies"] == 1
        assert data["ai_vs_human_ratio"] == 1.0

        # Messages per channel
        assert data["messages_per_channel"]["whatsapp"]["inbound"] == 1
        assert data["messages_per_channel"]["whatsapp"]["outbound"] == 1
        assert data["messages_per_channel"]["telegram"]["inbound"] == 1
        assert data["messages_per_channel"]["telegram"]["outbound"] == 1


class TestSettingsAndRBAC:
    async def test_agent_forbidden_from_admin_settings(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Agents cannot access channel settings, system settings, or knowledge base management."""
        _, agent_token = await create_user(db_session, "agent")
        headers = {"Authorization": f"Bearer {agent_token}"}

        r1 = await client.get(CHANNELS_SETTINGS_URL, headers=headers)
        assert r1.status_code == 403

        r2 = await client.get(SYSTEM_SETTINGS_URL, headers=headers)
        assert r2.status_code == 403

        r3 = await client.get(KB_URL, headers=headers)
        assert r3.status_code == 403

    async def test_channel_settings_credentials_redacted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Admin viewing channel credentials receives redacted text (••••1234), never plaintext."""
        _, admin_token = await create_user(db_session, "admin")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Store encrypted Telegram config in DB
        plain_creds = {"bot_token": "123456:secretToken9999", "webhook_secret": "mySecretKey8888"}
        encrypted = encrypt_value(json.dumps(plain_creds))
        cfg = ChannelConfig(channel="telegram", is_active=True, credentials_encrypted=encrypted)
        db_session.add(cfg)
        await db_session.commit()

        resp = await client.get(CHANNELS_SETTINGS_URL, headers=headers)
        assert resp.status_code == 200
        channels = {it["channel"]: it for it in resp.json()}
        tg_creds = channels["telegram"]["credentials"]

        # Secrets must be redacted
        assert "123456:secretToken9999" not in str(resp.json())
        assert tg_creds["bot_token"] == "••••9999"
        assert tg_creds["webhook_secret"] == "••••8888"

    async def test_channel_settings_partial_update(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """PUT /settings/channels/{channel} updates provided fields and preserves omitted ones."""
        _, admin_token = await create_user(db_session, "admin")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create initial config
        r1 = await client.put(
            f"{CHANNELS_SETTINGS_URL}/whatsapp",
            json={
                "is_active": True,
                "credentials": {
                    "app_secret": "initial_secret_1111",
                    "verify_token": "initial_verify_2222",
                },
            },
            headers=headers,
        )
        assert r1.status_code == 200
        assert r1.json()["credentials"]["app_secret"] == "••••1111"
        assert r1.json()["credentials"]["verify_token"] == "••••2222"

        # 2. Update only verify_token; app_secret must be preserved
        r2 = await client.put(
            f"{CHANNELS_SETTINGS_URL}/whatsapp",
            json={
                "credentials": {
                    "verify_token": "updated_verify_3333",
                },
            },
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["credentials"]["app_secret"] == "••••1111"
        assert r2.json()["credentials"]["verify_token"] == "••••3333"

    async def test_system_settings_crud(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Admin can read and update system prompt and confidence threshold."""
        _, admin_token = await create_user(db_session, "admin")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Read default
        r1 = await client.get(SYSTEM_SETTINGS_URL, headers=headers)
        assert r1.status_code == 200

        # Update
        r2 = await client.put(
            SYSTEM_SETTINGS_URL,
            json={
                "system_prompt": "You are a customer satisfaction champion.",
                "confidence_threshold": 0.85,
                "global_ai_enabled": False,
            },
            headers=headers,
        )
        assert r2.status_code == 200
        data = r2.json()
        assert data["system_prompt"] == "You are a customer satisfaction champion."
        assert data["confidence_threshold"] == 0.85
        assert data["global_ai_enabled"] is False

    async def test_knowledge_base_crud(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Admin can create, read, update, and delete knowledge base FAQs."""
        _, admin_token = await create_user(db_session, "admin")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create entry
        r1 = await client.post(
            KB_URL,
            json={
                "question": "Can I return clearance items?",
                "answer": "Clearance items are final sale.",
                "category": "Returns",
            },
            headers=headers,
        )
        assert r1.status_code == 201
        kb_id = r1.json()["id"]

        # 2. Get single
        r2 = await client.get(f"{KB_URL}/{kb_id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["question"] == "Can I return clearance items?"

        # 3. Update entry
        r3 = await client.put(
            f"{KB_URL}/{kb_id}",
            json={"answer": "Clearance items can be returned for store credit only."},
            headers=headers,
        )
        assert r3.status_code == 200
        assert "store credit" in r3.json()["answer"]

        # 4. List entries with search
        r4 = await client.get(f"{KB_URL}?search=clearance", headers=headers)
        assert r4.status_code == 200
        assert r4.json()["total"] == 1

        # 5. Delete entry
        r5 = await client.delete(f"{KB_URL}/{kb_id}", headers=headers)
        assert r5.status_code == 204

        # Verify deletion
        r6 = await client.get(f"{KB_URL}/{kb_id}", headers=headers)
        assert r6.status_code == 404


class TestSeedScriptIdempotency:
    async def test_seed_script_runs_twice_cleanly(self):
        """Running seed() multiple times is idempotent and does not error."""
        # Run 1
        await seed()

        # Run 2
        await seed()
