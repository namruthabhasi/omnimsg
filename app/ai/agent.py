from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import BaseLLMProvider
from app.ai.factory import get_llm_provider
from app.channels.manager import channel_registry
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message
from app.models.system_settings import SystemSettings

logger = structlog.get_logger()


class AIAgentService:
    """Orchestrates AI replies, grounding context, and human fallback."""

    def __init__(
        self,
        db: AsyncSession,
        llm_provider: Optional[BaseLLMProvider] = None,
    ) -> None:
        self.db = db
        self.llm_provider = llm_provider

    async def process_inbound_message(
        self,
        inbound_message_id: str,
    ) -> Optional[Message]:
        """
        Processes an inbound message: loads context, calls LLM, sends reply,
        and saves outbound message. Returns the created outbound message, or None if skipped.
        """
        # Fetch inbound message
        stmt_msg = select(Message).where(Message.id == inbound_message_id)
        res_msg = await self.db.execute(stmt_msg)
        inbound_msg = res_msg.scalar_one_or_none()

        if not inbound_msg or inbound_msg.direction != "inbound":
            return None

        # Fetch conversation
        stmt_conv = select(Conversation).where(
            Conversation.id == inbound_msg.conversation_id
        )
        res_conv = await self.db.execute(stmt_conv)
        conv = res_conv.scalar_one_or_none()

        if not conv:
            return None

        # Guard: Never reply twice to the same inbound message
        expected_ai_msg_id = f"ai-reply-{inbound_msg.id}"
        stmt_dup = select(Message).where(
            Message.channel == conv.channel,
            Message.external_message_id == expected_ai_msg_id,
        )
        res_dup = await self.db.execute(stmt_dup)
        if res_dup.scalar_one_or_none() is not None:
            logger.info(
                "Skipping duplicate AI reply",
                inbound_message_id=inbound_msg.id,
            )
            return None

        # Check conversation AI toggle (human takeover)
        if not conv.ai_enabled:
            logger.info("AI disabled for conversation", conversation_id=conv.id)
            return None

        # Fetch system settings for global AI toggle, system prompt, and fallback
        stmt_settings = select(SystemSettings).limit(1)
        res_settings = await self.db.execute(stmt_settings)
        sys_settings = res_settings.scalar_one_or_none()

        global_ai_enabled = (
            sys_settings.global_ai_enabled
            if sys_settings is not None
            else True
        )
        if not global_ai_enabled:
            logger.info("AI disabled globally")
            return None

        system_prompt = (
            sys_settings.system_prompt
            if sys_settings and sys_settings.system_prompt
            else settings.AI_SYSTEM_PROMPT
        )
        fallback_message = (
            sys_settings.fallback_message
            if sys_settings and sys_settings.fallback_message
            else settings.AI_FALLBACK_MESSAGE
        )

        # Load the last 10 messages for conversation context (ordered chronologically)
        stmt_history = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.timestamp.desc())
            .limit(10)
        )
        res_history = await self.db.execute(stmt_history)
        history_records = list(res_history.scalars().all())
        history_records.reverse()  # Oldest to newest

        formatted_messages = [
            {
                "role": "user" if m.direction == "inbound" else "assistant",
                "content": m.text,
            }
            for m in history_records
        ]

        # Load active Knowledge Base entries
        stmt_kb = select(KnowledgeBase).where(KnowledgeBase.is_active.is_(True))
        res_kb = await self.db.execute(stmt_kb)
        kb_entries = res_kb.scalars().all()
        kb_snippets = [f"Q: {kb.question}\nA: {kb.answer}" for kb in kb_entries]

        # Call LLM provider
        provider = self.llm_provider or get_llm_provider()
        reply_text = fallback_message
        needs_human = False

        try:
            llm_result = await provider.generate_reply(
                messages=formatted_messages,
                system_prompt=system_prompt,
                context=kb_snippets,
            )

            # Check confidence and needs_human flag
            if (
                llm_result.needs_human
                or llm_result.confidence < settings.AI_CONFIDENCE_THRESHOLD
            ):
                needs_human = True
                reply_text = fallback_message
            else:
                reply_text = llm_result.reply

        except Exception as exc:
            logger.warning("LLM generation failed, triggering fallback", error=str(exc))
            needs_human = True
            reply_text = fallback_message

        # If fallback triggered, update conversation status to needs_human
        if needs_human:
            conv.status = "needs_human"

        # Send reply via channel adapter
        adapter = channel_registry.get(conv.channel)
        delivery_success = True
        if adapter:
            try:
                delivery_success = await adapter.send_message(
                    recipient_id=conv.external_user_id,
                    text=reply_text,
                )
            except Exception as exc:
                logger.error("Failed to send outbound message via adapter", error=str(exc))
                delivery_success = False

        # Record AI outbound message in DB
        now = datetime.now(timezone.utc)
        conv.last_message_at = now

        ai_message = Message(
            conversation_id=conv.id,
            channel=conv.channel,
            external_message_id=expected_ai_msg_id,
            direction="outbound",
            sender_type="ai",
            text=reply_text,
            timestamp=now,
            raw_payload={
                "in_reply_to": inbound_msg.id,
                "needs_human": needs_human,
                "delivery_status": "delivered" if delivery_success else "failed",
            },
        )
        self.db.add(ai_message)

        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            return None

        # Broadcast real-time events to connected WebSocket clients
        try:
            from app.websockets.connection_manager import connection_manager
            from app.websockets.events import serialize_conversation, serialize_message

            await connection_manager.broadcast("message.new", serialize_message(ai_message))
            await connection_manager.broadcast("conversation.updated", serialize_conversation(conv))

            if needs_human:
                await connection_manager.broadcast(
                    "conversation.needs_human",
                    {
                        "conversation_id": conv.id,
                        "channel": conv.channel,
                        "external_user_id": conv.external_user_id,
                        "customer_name": conv.customer_name,
                        "reason": "fallback_triggered",
                        "inbound_message_id": inbound_msg.id,
                    },
                )
        except Exception as exc:
            logger.warning("Failed to broadcast WebSocket events for AI reply", error=str(exc))

        return ai_message
