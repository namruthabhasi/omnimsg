from __future__ import annotations

from typing import Optional, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.message import NormalizedMessage

logger = structlog.get_logger()


class MessagePipelineService:
    """Core message processing service responsible for ingest and deduplication."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create_conversation(
        self,
        channel: str,
        external_user_id: str,
        customer_name: Optional[str] = None,
    ) -> Conversation:
        stmt = select(Conversation).where(
            Conversation.channel == channel,
            Conversation.external_user_id == external_user_id,
        )
        result = await self.db.execute(stmt)
        conv = result.scalar_one_or_none()

        if conv is None:
            conv = Conversation(
                channel=channel,
                external_user_id=external_user_id,
                customer_name=customer_name,
                status="open",
                ai_enabled=True,
                unread_count=0,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(conv)
                    await self.db.flush()
            except IntegrityError:
                # Concurrent insertion race condition
                result = await self.db.execute(stmt)
                conv = result.scalar_one()
        elif customer_name and not conv.customer_name:
            conv.customer_name = customer_name
            await self.db.flush()

        return conv

    async def ingest_inbound_message(
        self,
        normalized: NormalizedMessage,
    ) -> Tuple[Optional[Message], bool, Conversation]:
        """
        Ingest an inbound message with strict deduplication via unique constraint.
        Returns: (saved_message, is_duplicate, conversation)
        """
        conv = await self.get_or_create_conversation(
            channel=normalized.channel,
            external_user_id=normalized.external_user_id,
            customer_name=normalized.customer_name,
        )

        # Amendment 7: A closed conversation reopens when a new inbound message arrives
        if conv.status == "closed":
            conv.status = "open"

        # Increment unread count and update last message timestamp
        conv.unread_count += 1
        conv.last_message_at = normalized.timestamp

        msg = Message(
            conversation_id=conv.id,
            channel=normalized.channel,
            external_message_id=normalized.external_message_id,
            direction="inbound",
            sender_type="customer",
            text=normalized.text,
            raw_payload=normalized.raw_payload,
            timestamp=normalized.timestamp,
        )

        try:
            # Amendment 5: Deduplicate via the unique index and catch IntegrityError
            async with self.db.begin_nested():
                self.db.add(msg)
                await self.db.flush()
        except IntegrityError:
            logger.info(
                "Duplicate message ignored",
                channel=normalized.channel,
                external_message_id=normalized.external_message_id,
            )
            return None, True, conv

        # Broadcast real-time events to connected WebSocket clients
        try:
            from app.websockets.connection_manager import connection_manager
            from app.websockets.events import serialize_conversation, serialize_message

            await connection_manager.broadcast("message.new", serialize_message(msg))
            await connection_manager.broadcast("conversation.updated", serialize_conversation(conv))
        except Exception as exc:
            logger.warning("Failed to broadcast WebSocket events for inbound message", error=str(exc))

        return msg, False, conv
