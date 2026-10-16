from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LastMessagePreview(BaseModel):
    id: str
    text: str
    direction: str
    sender_type: str
    timestamp: datetime


class ConversationListItem(BaseModel):
    id: str
    channel: str
    external_user_id: str
    customer_name: Optional[str] = None
    status: str
    ai_enabled: bool
    sentiment: Optional[str] = None
    intent: Optional[str] = None
    unread_count: int
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    last_message: Optional[LastMessagePreview] = None

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    items: List[ConversationListItem]
    total: int
    page: int
    limit: int
    pages: int


class MessageDetail(BaseModel):
    id: str
    conversation_id: str
    channel: str
    external_message_id: str
    direction: str
    sender_type: str
    text: str
    timestamp: datetime
    created_at: datetime
    delivery_status: Optional[str] = "delivered"


class ConversationDetailResponse(BaseModel):
    id: str
    channel: str
    external_user_id: str
    customer_name: Optional[str] = None
    status: str
    ai_enabled: bool
    sentiment: Optional[str] = None
    intent: Optional[str] = None
    unread_count: int
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    messages: List[MessageDetail]
    total_messages: int
    page: int
    limit: int


class ConversationUpdate(BaseModel):
    status: Optional[Literal["open", "needs_human", "closed"]] = None
    ai_enabled: Optional[bool] = None
    sentiment: Optional[str] = None
    intent: Optional[str] = None


class HumanReplyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)
    keep_ai_enabled: bool = False
