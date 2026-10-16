from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ai.agent import AIAgentService
from app.ai.mock_provider import MockLLMProvider
from app.channels.manager import channel_registry
from app.channels.telegram import TelegramAdapter
from app.core.security import create_access_token
from app.main import app
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.message import NormalizedMessage
from app.services.message_pipeline import MessagePipelineService
from app.websockets.connection_manager import connection_manager

pytestmark = pytest.mark.asyncio

WS_URL = "/api/v1/ws"
TELEGRAM_WEBHOOK_URL = "/api/v1/webhooks/telegram"
TEST_SECRET = "test_telegram_secret_token_123"
TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture(autouse=True)
def setup_channel(monkeypatch):
    telegram_adapter = TelegramAdapter(bot_token=TEST_BOT_TOKEN, webhook_secret=TEST_SECRET)
    channel_registry.register("telegram", telegram_adapter)


async def create_test_user(db_session) -> User:
    """Helper creating a test user in DB and committing so separate sessions see it."""
    import uuid
    user = User(
        email=f"ws_user_{uuid.uuid4().hex}@example.com",
        hashed_password="hashed_pwd",
        full_name="WS Tester",
        role="agent",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


class TestWebSocketAuth:
    async def test_valid_auth_succeeds(self, db_session):
        """Client authenticates successfully via first-message handshake."""
        user = await create_test_user(db_session)
        token = create_access_token(subject=user.id, role=user.role)

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws:
                ws.send_json({"type": "auth", "token": token})
                resp = ws.receive_json()
                assert resp == {"type": "auth.ok"}

                # Heartbeat ping/pong
                ws.send_json({"type": "ping"})
                pong = ws.receive_json()
                assert pong == {"type": "pong"}

    async def test_invalid_token_rejected(self):
        """Invalid JWT is rejected and connection closed with policy violation (1008)."""
        with TestClient(app) as tc:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with tc.websocket_connect(WS_URL) as ws:
                    ws.send_json({"type": "auth", "token": "not.a.valid.jwt"})
                    ws.receive_json()
            assert exc_info.value.code == 1008

    async def test_expired_token_rejected(self, db_session):
        """Expired JWT is rejected with code 1008."""
        user = await create_test_user(db_session)
        expired_token = create_access_token(
            subject=user.id,
            role=user.role,
            expires_delta=timedelta(seconds=-10),  # expired 10 seconds ago
        )

        with TestClient(app) as tc:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with tc.websocket_connect(WS_URL) as ws:
                    ws.send_json({"type": "auth", "token": expired_token})
                    ws.receive_json()
            assert exc_info.value.code == 1008

    async def test_unauthenticated_message_rejected(self):
        """Sending a non-auth message before authentication closes the connection."""
        with TestClient(app) as tc:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with tc.websocket_connect(WS_URL) as ws:
                    ws.send_json({"type": "ping"})
                    ws.receive_json()
            assert exc_info.value.code == 1008

    async def test_auth_timeout_closes_connection(self):
        """Failing to send an auth message within timeout closes socket with code 1008."""
        # Temporarily patch timeout to 0.1s for fast test execution
        with patch("app.api.v1.websocket.AUTH_TIMEOUT_SECONDS", 0.1):
            with TestClient(app) as tc:
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    with tc.websocket_connect(WS_URL) as ws:
                        # Wait without sending anything
                        ws.receive_text()
                assert exc_info.value.code == 1008

    async def test_reauth_resets_token_expiry(self, db_session):
        """Client can send an auth message during active connection to refresh token."""
        user = await create_test_user(db_session)
        token1 = create_access_token(subject=user.id, role=user.role)
        token2 = create_access_token(subject=user.id, role=user.role)

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws:
                ws.send_json({"type": "auth", "token": token1})
                assert ws.receive_json() == {"type": "auth.ok"}

                # Re-auth with token2
                ws.send_json({"type": "auth", "token": token2})
                assert ws.receive_json() == {"type": "auth.ok"}


class TestWebSocketEvents:
    async def test_inbound_message_broadcasts_message_new(self, db_session):
        """Inbound message ingestion broadcasts message.new and conversation.updated."""
        user = await create_test_user(db_session)
        token = create_access_token(subject=user.id, role=user.role)

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws:
                ws.send_json({"type": "auth", "token": token})
                assert ws.receive_json() == {"type": "auth.ok"}

                # Ingest inbound message via pipeline
                pipeline = MessagePipelineService(db_session)
                inbound = NormalizedMessage(
                    channel="telegram",
                    external_user_id="tg_client_1",
                    external_message_id="ext_msg_event_1",
                    customer_name="Alice",
                    text="Hello realtime!",
                    timestamp=datetime.now(timezone.utc),
                )
                await pipeline.ingest_inbound_message(inbound)

                # Receive message.new
                event1 = ws.receive_json()
                assert event1["type"] == "message.new"
                assert event1["data"]["text"] == "Hello realtime!"
                assert event1["data"]["direction"] == "inbound"
                assert "timestamp" in event1

                # Receive conversation.updated
                event2 = ws.receive_json()
                assert event2["type"] == "conversation.updated"
                assert event2["data"]["channel"] == "telegram"
                assert event2["data"]["unread_count"] == 1

    async def test_ai_reply_produces_message_new_and_needs_human_on_fallback(
        self,
        db_session,
    ):
        """AI fallback triggers message.new (outbound AI) and conversation.needs_human."""
        user = await create_test_user(db_session)
        token = create_access_token(subject=user.id, role=user.role)

        mock_llm = MockLLMProvider(mode="low_confidence", confidence=0.2)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws:
                ws.send_json({"type": "auth", "token": token})
                assert ws.receive_json() == {"type": "auth.ok"}

                conv = Conversation(
                    channel="telegram",
                    external_user_id="user_ws_fallback",
                    customer_name="Bob",
                    status="open",
                    ai_enabled=True,
                    unread_count=1,
                )
                db_session.add(conv)
                await db_session.flush()

                inbound = Message(
                    conversation_id=conv.id,
                    channel="telegram",
                    external_message_id="msg_ws_fallback_1",
                    direction="inbound",
                    sender_type="customer",
                    text="Complex inquiry",
                    timestamp=datetime.now(timezone.utc),
                )
                db_session.add(inbound)
                await db_session.flush()

                # Process AI reply (mocking HTTP outbound)
                with patch("httpx.AsyncClient.post") as mock_post:
                    mock_post.return_value = AsyncMock(status_code=200)
                    await agent.process_inbound_message(inbound.id)

                # Collect broadcast events
                events = []
                for _ in range(3):
                    events.append(ws.receive_json())

                event_types = [e["type"] for e in events]
                assert "message.new" in event_types
                assert "conversation.updated" in event_types
                assert "conversation.needs_human" in event_types

                needs_human_event = next(e for e in events if e["type"] == "conversation.needs_human")
                assert needs_human_event["data"]["conversation_id"] == conv.id
                assert needs_human_event["data"]["reason"] == "fallback_triggered"

    async def test_multiple_connected_clients_receive_event(self, db_session):
        """Multiple connected clients all receive broadcast messages."""
        user1 = await create_test_user(db_session)
        user2 = await create_test_user(db_session)
        token1 = create_access_token(subject=user1.id, role=user1.role)
        token2 = create_access_token(subject=user2.id, role=user2.role)

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws1, tc.websocket_connect(WS_URL) as ws2:
                ws1.send_json({"type": "auth", "token": token1})
                assert ws1.receive_json() == {"type": "auth.ok"}

                ws2.send_json({"type": "auth", "token": token2})
                assert ws2.receive_json() == {"type": "auth.ok"}

                # Broadcast test event
                await connection_manager.broadcast("test.event", {"hello": "world"})

                msg1 = ws1.receive_json()
                msg2 = ws2.receive_json()

                assert msg1["type"] == "test.event"
                assert msg1["data"] == {"hello": "world"}
                assert msg2["type"] == "test.event"
                assert msg2["data"] == {"hello": "world"}

    async def test_broken_client_does_not_stop_delivery_to_others(self, db_session):
        """A disconnected or failing client does not prevent other clients from receiving events."""
        user1 = await create_test_user(db_session)
        user2 = await create_test_user(db_session)
        token1 = create_access_token(subject=user1.id, role=user1.role)
        token2 = create_access_token(subject=user2.id, role=user2.role)

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws1, tc.websocket_connect(WS_URL) as ws2:
                ws1.send_json({"type": "auth", "token": token1})
                assert ws1.receive_json() == {"type": "auth.ok"}

                ws2.send_json({"type": "auth", "token": token2})
                assert ws2.receive_json() == {"type": "auth.ok"}

                # Close ws1 abruptly
                ws1.close()

                # Broadcast event - must succeed and be received by ws2
                await connection_manager.broadcast("test.resilience", {"status": "ok"})

                msg2 = ws2.receive_json()
                assert msg2["type"] == "test.resilience"
                assert msg2["data"] == {"status": "ok"}

    async def test_disconnect_cleans_up_registry(self, db_session):
        """Disconnecting a WebSocket client cleanly removes it from connection_manager."""
        user = await create_test_user(db_session)
        token = create_access_token(subject=user.id, role=user.role)

        initial_count = connection_manager.get_active_count()

        with TestClient(app) as tc:
            with tc.websocket_connect(WS_URL) as ws:
                ws.send_json({"type": "auth", "token": token})
                assert ws.receive_json() == {"type": "auth.ok"}
                assert connection_manager.get_active_count() == initial_count + 1

        # After exiting context, client is disconnected
        assert connection_manager.get_active_count() == initial_count
