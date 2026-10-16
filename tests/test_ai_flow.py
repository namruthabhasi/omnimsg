from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AIAgentService
from app.ai.mock_provider import MockLLMProvider
from app.channels.manager import channel_registry
from app.channels.mock import MockChannelAdapter
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message
from app.models.system_settings import SystemSettings
from app.schemas.message import NormalizedMessage
from app.services.message_pipeline import MessagePipelineService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_adapter() -> MockChannelAdapter:
    adapter = MockChannelAdapter()
    channel_registry.register("mock", adapter)
    return adapter


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    return MockLLMProvider()


class TestAIFlow:
    async def test_normal_ai_reply(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """Inbound message triggers normal AI reply through adapter and saves outbound message."""
        mock_adapter.reset()
        mock_llm.reset()
        mock_llm.set_mode("normal", reply="Hello! How can I help you today?")

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_123",
            external_message_id="msg_001",
            text="Hi, I need assistance.",
            timestamp=datetime.now(timezone.utc),
        )
        msg, is_dup, conv = await pipeline.ingest_inbound_message(inbound)
        assert msg is not None
        assert is_dup is False
        assert conv.status == "open"
        assert conv.unread_count == 1

        # Process AI reply
        ai_msg = await agent.process_inbound_message(msg.id)
        assert ai_msg is not None
        assert ai_msg.direction == "outbound"
        assert ai_msg.sender_type == "ai"
        assert ai_msg.text == "Hello! How can I help you today?"
        assert conv.status == "open"

        # Verify adapter sent the reply
        assert len(mock_adapter.sent_messages) == 1
        assert mock_adapter.sent_messages[0]["recipient_id"] == "user_123"
        assert mock_adapter.sent_messages[0]["text"] == "Hello! How can I help you today?"

    async def test_ai_disabled_globally(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """When global_ai_enabled is False, no AI reply is dispatched."""
        mock_adapter.reset()
        mock_llm.reset()

        # Disable global AI in system settings
        sys_settings = SystemSettings(
            global_ai_enabled=False,
            system_prompt="Test",
            fallback_message="Fallback",
        )
        db_session.add(sys_settings)
        await db_session.flush()

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_global_off",
            external_message_id="msg_global_off_1",
            text="Anyone there?",
            timestamp=datetime.now(timezone.utc),
        )
        msg, _, _ = await pipeline.ingest_inbound_message(inbound)
        assert msg is not None

        ai_msg = await agent.process_inbound_message(msg.id)
        assert ai_msg is None
        assert len(mock_adapter.sent_messages) == 0

    async def test_ai_disabled_per_conversation(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """When conversation.ai_enabled is False (human takeover), AI skips replying."""
        mock_adapter.reset()
        mock_llm.reset()

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        conv = await pipeline.get_or_create_conversation(
            channel="mock", external_user_id="user_takeover"
        )
        conv.ai_enabled = False  # Human agent paused AI
        await db_session.flush()

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_takeover",
            external_message_id="msg_takeover_1",
            text="Hello agent?",
            timestamp=datetime.now(timezone.utc),
        )
        msg, _, _ = await pipeline.ingest_inbound_message(inbound)

        ai_msg = await agent.process_inbound_message(msg.id)
        assert ai_msg is None
        assert len(mock_adapter.sent_messages) == 0

    async def test_low_confidence_fallback(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """Low confidence triggers fallback message and marks status as needs_human."""
        mock_adapter.reset()
        mock_llm.set_mode("low_confidence", confidence=0.3)

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_low_conf",
            external_message_id="msg_low_conf",
            text="Can you explain quantum chromodynamics for my invoice?",
            timestamp=datetime.now(timezone.utc),
        )
        msg, _, conv = await pipeline.ingest_inbound_message(inbound)

        ai_msg = await agent.process_inbound_message(msg.id)
        assert ai_msg is not None
        assert conv.status == "needs_human"
        assert "human agent" in ai_msg.text.lower()
        assert len(mock_adapter.sent_messages) == 1
        assert "human agent" in mock_adapter.sent_messages[0]["text"].lower()

    async def test_llm_exception_fallback(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """LLM exception triggers fallback message and marks status as needs_human."""
        mock_adapter.reset()
        mock_llm.set_mode("exception")

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_exc",
            external_message_id="msg_exc",
            text="Test exception handling",
            timestamp=datetime.now(timezone.utc),
        )
        msg, _, conv = await pipeline.ingest_inbound_message(inbound)

        ai_msg = await agent.process_inbound_message(msg.id)
        assert ai_msg is not None
        assert conv.status == "needs_human"
        assert len(mock_adapter.sent_messages) == 1

    async def test_malformed_output_fallback(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """Malformed output triggers fallback and sets needs_human."""
        mock_adapter.reset()
        mock_llm.set_mode("malformed")

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_malformed",
            external_message_id="msg_malformed",
            text="Trigger malformed output",
            timestamp=datetime.now(timezone.utc),
        )
        msg, _, conv = await pipeline.ingest_inbound_message(inbound)

        ai_msg = await agent.process_inbound_message(msg.id)
        assert ai_msg is not None
        assert conv.status == "needs_human"

    async def test_duplicate_inbound_message_ignored(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
    ):
        """Duplicate message with same (channel, external_message_id) is ignored via IntegrityError."""
        pipeline = MessagePipelineService(db_session)

        inbound1 = NormalizedMessage(
            channel="mock",
            external_user_id="user_dup",
            external_message_id="duplicate_ext_id_123",
            text="First send",
            timestamp=datetime.now(timezone.utc),
        )
        msg1, is_dup1, _ = await pipeline.ingest_inbound_message(inbound1)
        assert msg1 is not None
        assert is_dup1 is False

        # Attempt to insert identical channel + external_message_id
        inbound2 = NormalizedMessage(
            channel="mock",
            external_user_id="user_dup",
            external_message_id="duplicate_ext_id_123",
            text="Retry from platform",
            timestamp=datetime.now(timezone.utc),
        )
        msg2, is_dup2, _ = await pipeline.ingest_inbound_message(inbound2)
        assert msg2 is None
        assert is_dup2 is True

        # Ensure only 1 message exists in DB
        stmt = select(Message).where(Message.external_message_id == "duplicate_ext_id_123")
        res = await db_session.execute(stmt)
        messages = res.scalars().all()
        assert len(messages) == 1

    async def test_closed_conversation_reopening(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
    ):
        """A closed conversation reopens when a new inbound message arrives."""
        pipeline = MessagePipelineService(db_session)

        conv = await pipeline.get_or_create_conversation(
            channel="mock", external_user_id="user_reopen"
        )
        conv.status = "closed"
        await db_session.flush()
        assert conv.status == "closed"

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_reopen",
            external_message_id="reopen_msg_1",
            text="I have another question!",
            timestamp=datetime.now(timezone.utc),
        )
        _, _, updated_conv = await pipeline.ingest_inbound_message(inbound)
        assert updated_conv.status == "open"

    async def test_history_limited_to_10_messages(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """AI context includes at most the last 10 messages in chronological order."""
        mock_adapter.reset()
        mock_llm.reset()

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        conv = await pipeline.get_or_create_conversation(
            channel="mock", external_user_id="user_history"
        )

        # Seed 15 messages (timestamp ascending)
        last_msg = None
        for i in range(15):
            inbound = NormalizedMessage(
                channel="mock",
                external_user_id="user_history",
                external_message_id=f"hist_msg_{i}",
                text=f"Message number {i}",
                timestamp=datetime(2026, 1, 1, 10, i, 0, tzinfo=timezone.utc),
            )
            last_msg, _, _ = await pipeline.ingest_inbound_message(inbound)

        assert last_msg is not None

        # Process reply for the 15th message
        await agent.process_inbound_message(last_msg.id)

        # Check call history passed to LLM
        assert len(mock_llm.call_history) == 1
        llm_messages = mock_llm.call_history[0]["messages"]
        assert len(llm_messages) == 10
        # The messages should be indices 5 through 14
        assert llm_messages[0]["content"] == "Message number 5"
        assert llm_messages[-1]["content"] == "Message number 14"

    async def test_never_reply_twice_to_same_inbound_message(
        self,
        db_session: AsyncSession,
        mock_adapter: MockChannelAdapter,
        mock_llm: MockLLMProvider,
    ):
        """Calling process_inbound_message twice on the same message skips the second call."""
        mock_adapter.reset()
        mock_llm.reset()

        pipeline = MessagePipelineService(db_session)
        agent = AIAgentService(db_session, llm_provider=mock_llm)

        inbound = NormalizedMessage(
            channel="mock",
            external_user_id="user_once",
            external_message_id="msg_once_1",
            text="Hello once",
            timestamp=datetime.now(timezone.utc),
        )
        msg, _, _ = await pipeline.ingest_inbound_message(inbound)

        # First call
        ai_msg1 = await agent.process_inbound_message(msg.id)
        assert ai_msg1 is not None
        assert len(mock_adapter.sent_messages) == 1

        # Second call for the same inbound message
        ai_msg2 = await agent.process_inbound_message(msg.id)
        assert ai_msg2 is None
        # Adapter sent count must still be 1
        assert len(mock_adapter.sent_messages) == 1
