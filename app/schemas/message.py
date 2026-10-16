from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


ChannelType = Literal["whatsapp", "instagram", "telegram", "messenger", "mock"]
MessageDirection = Literal["inbound", "outbound"]
SenderType = Literal["customer", "ai", "human"]


class NormalizedMessage(BaseModel):
    """Platform-agnostic message representation."""

    channel: str
    external_user_id: str
    external_message_id: str
    customer_name: Optional[str] = None
    text: str
    timestamp: datetime
    direction: MessageDirection = "inbound"
    sender_type: SenderType = "customer"
    raw_payload: Optional[dict] = None


class MessageCreate(BaseModel):
    """Schema for sending an outbound manual reply."""

    text: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    channel: str
    external_message_id: str
    direction: str
    sender_type: str
    text: str
    timestamp: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
