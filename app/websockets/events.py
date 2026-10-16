from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from app.models.conversation import Conversation
from app.models.message import Message


def create_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Wraps event data in the standard envelope: {type, data, timestamp}."""
    return {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def serialize_message(message: Message) -> Dict[str, Any]:
    """Serializes a Message ORM instance into standard event data shape."""
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "channel": message.channel,
        "external_message_id": message.external_message_id,
        "direction": message.direction,
        "sender_type": message.sender_type,
        "text": message.text,
        "timestamp": (
            message.timestamp.isoformat()
            if hasattr(message.timestamp, "isoformat")
            else str(message.timestamp)
        ),
        "created_at": (
            message.created_at.isoformat()
            if hasattr(message.created_at, "isoformat")
            else str(message.created_at)
        ),
    }


def serialize_conversation(conversation: Conversation) -> Dict[str, Any]:
    """Serializes a Conversation ORM instance into standard event data shape."""
    return {
        "id": conversation.id,
        "channel": conversation.channel,
        "external_user_id": conversation.external_user_id,
        "customer_name": conversation.customer_name,
        "status": conversation.status,
        "ai_enabled": conversation.ai_enabled,
        "sentiment": conversation.sentiment,
        "intent": conversation.intent,
        "unread_count": conversation.unread_count,
        "last_message_at": (
            conversation.last_message_at.isoformat()
            if conversation.last_message_at and hasattr(conversation.last_message_at, "isoformat")
            else None
        ),
    }
