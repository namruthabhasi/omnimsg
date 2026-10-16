from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.instagram import InstagramAdapter
from app.channels.manager import channel_registry
from app.channels.messenger import MessengerAdapter
from app.channels.whatsapp import WhatsAppAdapter
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.message import Message

pytestmark = pytest.mark.asyncio

META_APP_SECRET = "test_meta_app_secret_12345"
META_VERIFY_TOKEN = "test_meta_verify_token_54321"


@pytest.fixture(autouse=True)
def configure_meta_credentials(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", META_APP_SECRET)
    monkeypatch.setattr(settings, "META_VERIFY_TOKEN", META_VERIFY_TOKEN)
    monkeypatch.setattr(settings, "ENABLE_SIMULATOR", True)

    wa_adapter = WhatsAppAdapter(
        app_secret=META_APP_SECRET,
        verify_token=META_VERIFY_TOKEN,
        simulation_mode=True,
    )
    ig_adapter = InstagramAdapter(
        app_secret=META_APP_SECRET,
        verify_token=META_VERIFY_TOKEN,
        simulation_mode=True,
    )
    fb_adapter = MessengerAdapter(
        app_secret=META_APP_SECRET,
        verify_token=META_VERIFY_TOKEN,
        simulation_mode=True,
    )

    channel_registry.register("whatsapp", wa_adapter)
    channel_registry.register("instagram", ig_adapter)
    channel_registry.register("messenger", fb_adapter)


def sign_payload(payload_bytes: bytes, secret: str = META_APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


class TestMetaVerificationGET:
    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_get_verification_success(self, client: AsyncClient, channel: str):
        """GET with correct mode and verify_token returns hub.challenge plain text."""
        challenge_token = "unique_challenge_abc_123"
        resp = await client.get(
            f"/api/v1/webhooks/{channel}",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": META_VERIFY_TOKEN,
                "hub.challenge": challenge_token,
            },
        )
        assert resp.status_code == 200
        assert resp.text == challenge_token

    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_get_verification_wrong_token_403(self, client: AsyncClient, channel: str):
        """GET with wrong verify token returns 403."""
        resp = await client.get(
            f"/api/v1/webhooks/{channel}",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "ch",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_get_verification_wrong_mode_403(self, client: AsyncClient, channel: str):
        """GET with wrong hub.mode returns 403."""
        resp = await client.get(
            f"/api/v1/webhooks/{channel}",
            params={
                "hub.mode": "publish",
                "hub.verify_token": META_VERIFY_TOKEN,
                "hub.challenge": "ch",
            },
        )
        assert resp.status_code == 403


class TestMetaSignaturePOST:
    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_post_missing_signature_rejected_403(self, client: AsyncClient, channel: str):
        """POST without X-Hub-Signature-256 header returns 403."""
        resp = await client.post(
            f"/api/v1/webhooks/{channel}",
            content=b'{"object":"test"}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_post_invalid_signature_rejected_403(self, client: AsyncClient, channel: str):
        """POST with invalid X-Hub-Signature-256 returns 403."""
        resp = await client.post(
            f"/api/v1/webhooks/{channel}",
            content=b'{"object":"test"}',
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalidhex00000000000000000000000000000000000000000000000000000000",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_post_tampered_body_rejected_403(self, client: AsyncClient, channel: str):
        """POST where signature does not match body bytes returns 403."""
        sig = sign_payload(b'{"original":"data"}')
        resp = await client.post(
            f"/api/v1/webhooks/{channel}",
            content=b'{"tampered":"data"}',
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("channel", ["whatsapp", "instagram", "messenger"])
    async def test_malformed_json_returns_200(self, client: AsyncClient, channel: str):
        """Malformed JSON with valid signature returns 200 without crashing."""
        body = b"not-a-json-object-string"
        sig = sign_payload(body)
        resp = await client.post(
            f"/api/v1/webhooks/{channel}",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "invalid_payload"


class TestWhatsAppWebhook:
    async def test_whatsapp_message_ingestion(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """WhatsApp message creates conversation and inbound message with contact name."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "contacts": [
                                    {"profile": {"name": "Maria Garcia"}, "wa_id": "15551234567"}
                                ],
                                "messages": [
                                    {
                                        "from": "15551234567",
                                        "id": "wamid.test_wa_001",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": "Hello on WhatsApp!"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Verify DB
        stmt = select(Conversation).where(
            Conversation.channel == "whatsapp",
            Conversation.external_user_id == "15551234567",
        )
        res = await db_session.execute(stmt)
        conv = res.scalar_one_or_none()
        assert conv is not None
        assert conv.customer_name == "Maria Garcia"

        stmt_msg = select(Message).where(Message.external_message_id == "wamid.test_wa_001")
        res_msg = await db_session.execute(stmt_msg)
        msg = res_msg.scalar_one_or_none()
        assert msg is not None
        assert msg.text == "Hello on WhatsApp!"

    async def test_whatsapp_status_receipts_ignored(self, client: AsyncClient):
        """Delivery and read receipts in value.statuses are ignored with 200."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "statuses": [
                                    {
                                        "id": "wamid.001",
                                        "status": "delivered",
                                        "timestamp": "1700000001",
                                        "recipient_id": "15551234567",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ignored"}

    async def test_whatsapp_non_text_message_ignored(self, client: AsyncClient):
        """Image or sticker messages are ignored with 200."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "12345",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "from": "15551234567",
                                        "id": "wamid.image_001",
                                        "timestamp": "1700000000",
                                        "type": "image",
                                        "image": {"id": "img123"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ignored"}


class TestInstagramAndMessengerWebhooks:
    async def test_instagram_message_ingestion(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Instagram text message creates conversation and inbound message."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "ig_page_123",
                    "time": 1700000000000,
                    "messaging": [
                        {
                            "sender": {"id": "ig_user_456"},
                            "recipient": {"id": "ig_page_123"},
                            "timestamp": 1700000000000,
                            "message": {
                                "mid": "m_ig_mid_001",
                                "text": "Hello Instagram Direct!",
                                "is_echo": False,
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/instagram",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        stmt = select(Message).where(Message.external_message_id == "m_ig_mid_001")
        res = await db_session.execute(stmt)
        msg = res.scalar_one_or_none()
        assert msg is not None
        assert msg.channel == "instagram"
        assert msg.text == "Hello Instagram Direct!"

    async def test_messenger_message_ingestion(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Messenger text message creates conversation and message."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "fb_page_789",
                    "time": 1700000000000,
                    "messaging": [
                        {
                            "sender": {"id": "fb_user_101"},
                            "recipient": {"id": "fb_page_789"},
                            "timestamp": 1700000000000,
                            "message": {
                                "mid": "m_fb_mid_001",
                                "text": "Hello Facebook Messenger!",
                                "is_echo": False,
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/messenger",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200

        stmt = select(Message).where(Message.external_message_id == "m_fb_mid_001")
        res = await db_session.execute(stmt)
        msg = res.scalar_one_or_none()
        assert msg is not None
        assert msg.channel == "messenger"

    async def test_echo_events_ignored(self, client: AsyncClient):
        """Echo events (bot's own sent messages) are ignored with 200."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "ig_page_123",
                    "messaging": [
                        {
                            "sender": {"id": "ig_page_123"},
                            "recipient": {"id": "ig_user_456"},
                            "message": {
                                "mid": "m_echo_001",
                                "text": "Echo reply from agent",
                                "is_echo": True,
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/instagram",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ignored"}

    async def test_batched_payload_processes_all_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Batched payload containing multiple messages stores all of them."""
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "fb_page_batch",
                    "messaging": [
                        {
                            "sender": {"id": "user_batched_1"},
                            "recipient": {"id": "page_id"},
                            "message": {"mid": "m_batch_1", "text": "First batch item"},
                        },
                        {
                            "sender": {"id": "user_batched_2"},
                            "recipient": {"id": "page_id"},
                            "message": {"mid": "m_batch_2", "text": "Second batch item"},
                        },
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        resp = await client.post(
            "/api/v1/webhooks/messenger",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200

        res1 = await db_session.execute(select(Message).where(Message.external_message_id == "m_batch_1"))
        res2 = await db_session.execute(select(Message).where(Message.external_message_id == "m_batch_2"))
        assert res1.scalar_one_or_none() is not None
        assert res2.scalar_one_or_none() is not None

    async def test_duplicate_message_ignored(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Duplicate message ID returns 200 but only creates 1 message record."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "dup_ig_user"},
                            "recipient": {"id": "ig_page"},
                            "message": {"mid": "m_dup_001", "text": "Duplicate IG test"},
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        sig = sign_payload(body)

        # Post 1
        resp1 = await client.post(
            "/api/v1/webhooks/instagram",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp1.status_code == 200

        # Post 2 (retry)
        resp2 = await client.post(
            "/api/v1/webhooks/instagram",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp2.status_code == 200

        res = await db_session.execute(select(Message).where(Message.external_message_id == "m_dup_001"))
        assert len(res.scalars().all()) == 1
