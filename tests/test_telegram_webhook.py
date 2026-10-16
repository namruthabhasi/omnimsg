from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AIAgentService
from app.ai.mock_provider import MockLLMProvider
from app.channels.manager import channel_registry
from app.channels.telegram import TelegramAdapter, split_message
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.conversation import Conversation
from app.models.message import Message

pytestmark = pytest.mark.asyncio

TELEGRAM_WEBHOOK_URL = "/api/v1/webhooks/telegram"
TEST_SECRET = "test_telegram_secret_token_123"
TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture(autouse=True)
def configure_telegram_env(monkeypatch):
    """Ensure consistent test credentials across all tests."""
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", TEST_BOT_TOKEN)
    # Ensure telegram adapter in registry uses test credentials
    telegram_adapter = TelegramAdapter(bot_token=TEST_BOT_TOKEN, webhook_secret=TEST_SECRET)
    channel_registry.register("telegram", telegram_adapter)


def build_telegram_update(
    chat_id: int = 987654321,
    message_id: int = 101,
    text: str = "Hello from Telegram!",
    first_name: str = "Alice",
    last_name: str = "Smith",
    username: str = "alicesmith",
    is_bot: bool = False,
) -> dict:
    return {
        "update_id": 99999,
        "message": {
            "message_id": message_id,
            "date": 1700000000,
            "chat": {
                "id": chat_id,
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "type": "private",
            },
            "from": {
                "id": chat_id,
                "is_bot": is_bot,
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
            },
            "text": text,
        },
    }


class TestTelegramWebhook:
    async def test_missing_secret_token_rejected_403(self, client: AsyncClient):
        """Webhook request without secret token header must return 403."""
        payload = build_telegram_update()
        resp = await client.post(TELEGRAM_WEBHOOK_URL, json=payload)
        assert resp.status_code == 403
        assert "secret token" in resp.json()["detail"].lower()

    async def test_invalid_secret_token_rejected_403(self, client: AsyncClient):
        """Webhook request with wrong secret token header must return 403."""
        payload = build_telegram_update()
        resp = await client.post(
            TELEGRAM_WEBHOOK_URL,
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
        )
        assert resp.status_code == 403

    async def test_valid_secret_token_accepted(self, client: AsyncClient):
        """Valid secret token header returns 200 OK immediately."""
        payload = build_telegram_update(message_id=201)
        resp = await client.post(
            TELEGRAM_WEBHOOK_URL,
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_text_message_creates_conversation_and_message(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Inbound Telegram message creates both conversation and message records."""
        payload = build_telegram_update(
            chat_id=555123,
            message_id=301,
            text="How much does standard shipping cost?",
            first_name="Bob",
            last_name="Jones",
            username="bobjones",
        )
        resp = await client.post(
            TELEGRAM_WEBHOOK_URL,
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET},
        )
        assert resp.status_code == 200

        # Query database to verify persistence
        stmt_conv = select(Conversation).where(
            Conversation.channel == "telegram",
            Conversation.external_user_id == "555123",
        )
        res_conv = await db_session.execute(stmt_conv)
        conv = res_conv.scalar_one_or_none()
        assert conv is not None
        assert "Bob Jones" in conv.customer_name
        assert "@bobjones" in conv.customer_name
        assert conv.status == "open"
        assert conv.unread_count == 1

        stmt_msg = select(Message).where(Message.conversation_id == conv.id)
        res_msg = await db_session.execute(stmt_msg)
        messages = res_msg.scalars().all()
        assert len(messages) >= 1
        inbound = next(m for m in messages if m.direction == "inbound")
        assert inbound.text == "How much does standard shipping cost?"
        assert inbound.external_message_id == "555123:301"

    async def test_ai_reply_sent_and_stored(
        self,
        db_session: AsyncSession,
    ):
        """End-to-end AI reply flow sends outbound Telegram message and records it in DB."""
        mock_llm = MockLLMProvider(
            mode="normal", reply="Standard shipping is $5.99."
        )
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        # Create inbound conversation and message
        conv = Conversation(
            channel="telegram",
            external_user_id="777888",
            customer_name="Test Customer",
            status="open",
            ai_enabled=True,
            unread_count=1,
        )
        db_session.add(conv)
        await db_session.flush()

        inbound = Message(
            conversation_id=conv.id,
            channel="telegram",
            external_message_id="777888:401",
            direction="inbound",
            sender_type="customer",
            text="Shipping inquiry",
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(inbound)
        await db_session.flush()

        # Mock outbound Telegram HTTP call
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = httpx.Response(
                200, json={"ok": True, "result": {"message_id": 999}}
            )

            ai_msg = await agent.process_inbound_message(inbound.id)
            assert ai_msg is not None
            assert ai_msg.direction == "outbound"
            assert ai_msg.sender_type == "ai"
            assert ai_msg.text == "Standard shipping is $5.99."

            # Verify Telegram API was called
            mock_post.assert_called_once()
            call_url = str(mock_post.call_args[0][0])
            assert f"bot{TEST_BOT_TOKEN}/sendMessage" in call_url
            call_json = mock_post.call_args[1]["json"]
            assert call_json["chat_id"] == "777888"
            assert call_json["text"] == "Standard shipping is $5.99."

    async def test_duplicate_update_ignored(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Duplicate update with identical message_id returns 200 without second insertion."""
        payload = build_telegram_update(chat_id=123123, message_id=501, text="Duplicate test")

        # First post
        resp1 = await client.post(
            TELEGRAM_WEBHOOK_URL,
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET},
        )
        assert resp1.status_code == 200

        # Second post of identical payload
        resp2 = await client.post(
            TELEGRAM_WEBHOOK_URL,
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET},
        )
        assert resp2.status_code == 200

        # Assert only 1 inbound message was saved
        stmt = select(Message).where(Message.external_message_id == "123123:501")
        res = await db_session.execute(stmt)
        assert len(res.scalars().all()) == 1

    async def test_non_text_and_edited_updates_ignored(self, client: AsyncClient):
        """Non-text updates (photo, edited message, channel post, bot's own message) return 200 and are ignored."""
        headers = {"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET}

        # 1. Photo update (no text)
        photo_update = {
            "update_id": 1001,
            "message": {
                "message_id": 601,
                "date": 1700000000,
                "chat": {"id": 111, "type": "private"},
                "from": {"id": 111, "is_bot": False},
                "photo": [{"file_id": "xyz"}],
            },
        }
        resp = await client.post(TELEGRAM_WEBHOOK_URL, json=photo_update, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

        # 2. Edited message update
        edited_update = {
            "update_id": 1002,
            "edited_message": {
                "message_id": 602,
                "date": 1700000000,
                "chat": {"id": 111, "type": "private"},
                "text": "Edited text",
            },
        }
        resp = await client.post(TELEGRAM_WEBHOOK_URL, json=edited_update, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

        # 3. Bot's own message
        bot_update = build_telegram_update(chat_id=111, message_id=603, is_bot=True)
        resp = await client.post(TELEGRAM_WEBHOOK_URL, json=bot_update, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    async def test_telegram_api_failure_does_not_break_pipeline(
        self,
        db_session: AsyncSession,
    ):
        """When Telegram sendMessage fails (e.g. 500 error), pipeline records failed delivery safely."""
        agent = AIAgentService(
            db_session,
            llm_provider=MockLLMProvider(reply="Outbound reply"),
        )

        conv = Conversation(
            channel="telegram",
            external_user_id="999000",
            status="open",
            ai_enabled=True,
            unread_count=1,
        )
        db_session.add(conv)
        await db_session.flush()

        inbound = Message(
            conversation_id=conv.id,
            channel="telegram",
            external_message_id="999000:701",
            direction="inbound",
            sender_type="customer",
            text="Hello error test",
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(inbound)
        await db_session.flush()

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = httpx.Response(500, text="Internal Telegram Error")

            ai_msg = await agent.process_inbound_message(inbound.id)
            assert ai_msg is not None
            # Message is saved in DB with delivery_status = "failed"
            assert ai_msg.raw_payload.get("delivery_status") == "failed"

    async def test_long_outbound_text_split(self):
        """Long text (>4096 chars) splits into chunks under Telegram 4096 limit."""
        long_paragraph = "This is a detailed reply with lots of information. " * 100  # ~5200 chars
        assert len(long_paragraph) > 4096

        chunks = split_message(long_paragraph, max_length=4096)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 4096
        # Combined chunks should preserve the full text (ignoring boundary whitespace)
        assert "".join(chunks).replace(" ", "") == long_paragraph.replace(" ", "")

    async def test_ai_disabled_stores_inbound_without_reply(
        self,
        db_session: AsyncSession,
    ):
        """When ai_enabled is False on a conversation, inbound message is kept but no reply is generated."""
        mock_llm = MockLLMProvider()
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        conv = Conversation(
            channel="telegram",
            external_user_id="888111",
            status="open",
            ai_enabled=False,  # Human takeover
            unread_count=1,
        )
        db_session.add(conv)
        await db_session.flush()

        inbound = Message(
            conversation_id=conv.id,
            channel="telegram",
            external_message_id="888111:801",
            direction="inbound",
            sender_type="customer",
            text="Need human agent only",
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(inbound)
        await db_session.flush()

        ai_msg = await agent.process_inbound_message(inbound.id)
        assert ai_msg is None
        # Assert no outbound message was created
        stmt = select(Message).where(
            Message.conversation_id == conv.id,
            Message.direction == "outbound",
        )
        res = await db_session.execute(stmt)
        assert len(res.scalars().all()) == 0
