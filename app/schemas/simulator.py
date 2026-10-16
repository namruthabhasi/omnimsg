from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SimulatorRequest(BaseModel):
    """Payload for triggering a simulated inbound message."""

    channel: Literal["whatsapp", "instagram", "telegram", "messenger", "mock"]
    external_user_id: str = Field(..., min_length=1)
    customer_name: Optional[str] = None
    text: str = Field(..., min_length=1)
    external_message_id: Optional[str] = None


class SimulatorResponse(BaseModel):
    status: str
    channel: str
    external_user_id: str
    external_message_id: str
    conversation_id: str
    message_id: str
