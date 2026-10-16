from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.manager import channel_registry
from app.channels.mock import MockChannelAdapter
from app.core.security import create_access_token, hash_password
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def create_user_with_token(db: AsyncSession, role: str = "agent") -> tuple[User, str]:
    user = User(
        email=f"user_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password123"),
        full_name="Test User",
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    token = create_access_token(subject=user.id, role=user.role)
    return user, token


class TestConversationsAPI:
    async def test_unauthenticated_returns_401(self, client: AsyncClient):
        """Conversations endpoints require authentication."""
        resp = await client.get("/api/v1/conversations")
        assert resp.status_code == 401

    async def test_list_conversations_with_filters_and_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Filters by channel, status, unread_only, and paginates properly."""
        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        # Create sample conversations
        now = datetime.now(timezone.utc)
        conv1 = Conversation(
            channel="whatsapp",
            external_user_id="wa_01",
            customer_name="Alice Adams",
            status="open",
            ai_enabled=True,
            sentiment="positive",
            intent="pricing",
            unread_count=2,
            last_message_at=now,
        )
        conv2 = Conversation(
            channel="telegram",
            external_user_id="tg_02",
            customer_name="Bob Brown",
            status="needs_human",
            ai_enabled=False,
            sentiment="negative",
            intent="complaint",
            unread_count=0,
            last_message_at=now,
        )
        conv3 = Conversation(
            channel="whatsapp",
            external_user_id="wa_03",
            customer_name="Charlie Clark",
            status="closed",
            ai_enabled=True,
            unread_count=0,
            last_message_at=now,
        )
        db_session.add_all([conv1, conv2, conv3])
        await db_session.flush()

        # Add a message to conv1 to verify last_message preview
        msg1 = Message(
            conversation_id=conv1.id,
            channel="whatsapp",
            external_message_id="msg_prev_1",
            direction="inbound",
            sender_type="customer",
            text="Latest inquiry from Alice",
            timestamp=now,
        )
        db_session.add(msg1)
        await db_session.commit()

        # Filter by channel=whatsapp
        r1 = await client.get("/api/v1/conversations?channel=whatsapp", headers=headers)
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["total"] == 2
        assert all(it["channel"] == "whatsapp" for it in data1["items"])

        # Filter by status=needs_human
        r2 = await client.get("/api/v1/conversations?status=needs_human", headers=headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["total"] == 1
        assert data2["items"][0]["customer_name"] == "Bob Brown"

        # Filter by unread_only=true
        r3 = await client.get("/api/v1/conversations?unread_only=true", headers=headers)
        assert r3.status_code == 200
        data3 = r3.json()
        assert data3["total"] == 1
        assert data3["items"][0]["customer_name"] == "Alice Adams"
        # Check last-message preview
        assert data3["items"][0]["last_message"]["text"] == "Latest inquiry from Alice"

        # Pagination limit=1
        r4 = await client.get("/api/v1/conversations?limit=1&page=1", headers=headers)
        assert r4.status_code == 200
        data4 = r4.json()
        assert len(data4["items"]) == 1
        assert data4["pages"] == 3

    async def test_search_by_customer_name_and_message_text(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Case-insensitive search matches both customer name and message content."""
        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        conv = Conversation(
            channel="messenger",
            external_user_id="fb_user_search",
            customer_name="Diana Prince",
            status="open",
            ai_enabled=True,
            last_message_at=datetime.now(timezone.utc),
        )
        db_session.add(conv)
        await db_session.flush()

        msg = Message(
            conversation_id=conv.id,
            channel="messenger",
            external_message_id="msg_search_01",
            direction="inbound",
            sender_type="customer",
            text="I need help with quantum widgets urgently!",
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(msg)
        await db_session.commit()

        # Search by customer name (partial case-insensitive)
        r1 = await client.get("/api/v1/conversations?search=diana", headers=headers)
        assert r1.status_code == 200
        assert r1.json()["total"] == 1
        assert r1.json()["items"][0]["id"] == conv.id

        # Search by message text (case-insensitive keyword)
        r2 = await client.get("/api/v1/conversations?search=quantum", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["total"] == 1
        assert r2.json()["items"][0]["id"] == conv.id

        # Search non-existent
        r3 = await client.get("/api/v1/conversations?search=nonexistentterm", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["total"] == 0

    async def test_conversation_detail_and_messages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /conversations/{id} returns conversation with chronologically ordered messages."""
        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        conv = Conversation(
            channel="telegram",
            external_user_id="tg_detail_user",
            customer_name="Detail Tester",
            status="open",
            ai_enabled=True,
            last_message_at=datetime.now(timezone.utc),
        )
        db_session.add(conv)
        await db_session.flush()

        m1 = Message(
            conversation_id=conv.id,
            channel="telegram",
            external_message_id="dt_1",
            direction="inbound",
            sender_type="customer",
            text="First message",
            timestamp=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        m2 = Message(
            conversation_id=conv.id,
            channel="telegram",
            external_message_id="dt_2",
            direction="outbound",
            sender_type="ai",
            text="AI answer",
            timestamp=datetime(2026, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        )
        db_session.add_all([m1, m2])
        await db_session.commit()

        resp = await client.get(f"/api/v1/conversations/{conv.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == conv.id
        assert data["customer_name"] == "Detail Tester"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["text"] == "First message"
        assert data["messages"][1]["text"] == "AI answer"

    async def test_reset_unread_count(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """POST /conversations/{id}/read resets unread_count to 0."""
        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        conv = Conversation(
            channel="whatsapp",
            external_user_id="wa_unread",
            customer_name="Unread User",
            status="open",
            ai_enabled=True,
            unread_count=5,
        )
        db_session.add(conv)
        await db_session.commit()

        resp = await client.post(f"/api/v1/conversations/{conv.id}/read", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["unread_count"] == 0

        # Check in DB
        await db_session.refresh(conv)
        assert conv.unread_count == 0

    async def test_patch_conversation_status_and_ai_toggle(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """PATCH /conversations/{id} updates status and ai_enabled."""
        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        conv = Conversation(
            channel="whatsapp",
            external_user_id="wa_patch",
            status="open",
            ai_enabled=True,
        )
        db_session.add(conv)
        await db_session.commit()

        # Update to needs_human and pause AI
        resp = await client.patch(
            f"/api/v1/conversations/{conv.id}",
            json={"status": "needs_human", "ai_enabled": False, "sentiment": "negative"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "needs_human"
        assert data["ai_enabled"] is False
        assert data["sentiment"] == "negative"

    async def test_human_reply_auto_disables_ai(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Sending human reply dispatches message and automatically sets ai_enabled=False."""
        mock_adapter = MockChannelAdapter()
        channel_registry.register("mock", mock_adapter)

        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        conv = Conversation(
            channel="mock",
            external_user_id="user_takeover_reply",
            status="needs_human",
            ai_enabled=True,
        )
        db_session.add(conv)
        await db_session.commit()

        # Agent sends human reply
        resp = await client.post(
            f"/api/v1/conversations/{conv.id}/reply",
            json={"text": "Hello, I am a human agent taking over.", "keep_ai_enabled": False},
            headers=headers,
        )
        assert resp.status_code == 200
        msg_data = resp.json()
        assert msg_data["sender_type"] == "human"
        assert msg_data["direction"] == "outbound"
        assert msg_data["text"] == "Hello, I am a human agent taking over."

        # Verify adapter dispatched the message
        assert len(mock_adapter.sent_messages) == 1
        assert mock_adapter.sent_messages[0]["recipient_id"] == "user_takeover_reply"

        # Verify conversation state in DB: ai_enabled must be False, status transitions to open
        await db_session.refresh(conv)
        assert conv.ai_enabled is False
        assert conv.status == "open"

    async def test_human_reply_failure_flagged(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """When channel adapter send fails, human reply records failure flag and returns 502."""
        mock_adapter = MockChannelAdapter()
        mock_adapter.should_send_pass = False  # Simulate outbound platform failure
        channel_registry.register("mock", mock_adapter)

        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        conv = Conversation(
            channel="mock",
            external_user_id="user_fail_reply",
            status="open",
            ai_enabled=True,
        )
        db_session.add(conv)
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/conversations/{conv.id}/reply",
            json={"text": "This reply will fail delivery."},
            headers=headers,
        )
        assert resp.status_code == 502

        # Verify message was stored with failed delivery status in DB
        res = await db_session.execute(select(Message).where(Message.conversation_id == conv.id))
        msg = res.scalar_one_or_none()
        assert msg is not None
        assert msg.raw_payload.get("delivery_status") == "failed"

    async def test_unknown_id_returns_404(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Invalid or non-existent conversation UUID returns 404."""
        _, token = await create_user_with_token(db_session)
        headers = {"Authorization": f"Bearer {token}"}

        # Non-existent valid UUID
        fake_id = str(uuid.uuid4())
        resp1 = await client.get(f"/api/v1/conversations/{fake_id}", headers=headers)
        assert resp1.status_code == 404

        # Invalid format string
        resp2 = await client.get("/api/v1/conversations/not-a-uuid", headers=headers)
        assert resp2.status_code == 404
