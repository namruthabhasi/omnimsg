from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.channels.manager import channel_registry
from app.core.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.conversation import (
    ConversationDetailResponse,
    ConversationListItem,
    ConversationListResponse,
    ConversationUpdate,
    HumanReplyRequest,
    LastMessagePreview,
    MessageDetail,
)
from app.websockets.connection_manager import connection_manager
from app.websockets.events import serialize_conversation, serialize_message

logger = structlog.get_logger()

router = APIRouter(prefix="/conversations", tags=["Conversations"])


def validate_uuid(id_str: str) -> None:
    try:
        uuid.UUID(id_str)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List conversations with filtering, search, and pagination",
)
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    channel: Optional[str] = None,
    status: Optional[str] = None,
    ai_enabled: Optional[bool] = None,
    unread_only: bool = False,
    sentiment: Optional[str] = None,
    intent: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Unified Inbox listing conversations.
    Supports filtering by channel, status, ai_enabled, unread_only, sentiment, and intent.
    Supports case-insensitive search across customer names and message text.
    Orders conversations by last_message_at descending.
    Avoids N+1 queries for last-message previews.
    """
    conditions = []

    if channel:
        conditions.append(Conversation.channel == channel.lower())
    if status:
        conditions.append(Conversation.status == status)
    if ai_enabled is not None:
        conditions.append(Conversation.ai_enabled.is_(ai_enabled))
    if unread_only:
        conditions.append(Conversation.unread_count > 0)
    if sentiment:
        conditions.append(Conversation.sentiment == sentiment)
    if intent:
        conditions.append(Conversation.intent == intent)

    if search:
        search_pattern = f"%{search}%"
        # Search customer name OR message text
        msg_subq = select(Message.conversation_id).where(
            Message.text.ilike(search_pattern)
        )
        conditions.append(
            or_(
                Conversation.customer_name.ilike(search_pattern),
                Conversation.id.in_(msg_subq),
            )
        )

    # 1. Total count query
    count_stmt = select(func.count(distinct(Conversation.id)))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # 2. Paginated conversation records
    offset = (page - 1) * limit
    stmt = (
        select(Conversation)
        .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if conditions:
        stmt = stmt.where(*conditions)

    res = await db.execute(stmt)
    conversations = list(res.scalars().all())

    # 3. Fetch last messages in a single query (avoid N+1)
    last_messages_map = {}
    if conversations:
        conv_ids = [c.id for c in conversations]
        msg_stmt = (
            select(Message)
            .where(Message.conversation_id.in_(conv_ids))
            .order_by(Message.timestamp.desc())
        )
        msg_res = await db.execute(msg_stmt)
        for m in msg_res.scalars().all():
            if m.conversation_id not in last_messages_map:
                last_messages_map[m.conversation_id] = LastMessagePreview(
                    id=m.id,
                    text=m.text,
                    direction=m.direction,
                    sender_type=m.sender_type,
                    timestamp=m.timestamp,
                )

    items = []
    for c in conversations:
        item = ConversationListItem(
            id=c.id,
            channel=c.channel,
            external_user_id=c.external_user_id,
            customer_name=c.customer_name,
            status=c.status,
            ai_enabled=c.ai_enabled,
            sentiment=c.sentiment,
            intent=c.intent,
            unread_count=c.unread_count,
            last_message_at=c.last_message_at,
            created_at=c.created_at,
            updated_at=c.updated_at,
            last_message=last_messages_map.get(c.id),
        )
        items.append(item)

    pages = math.ceil(total / limit) if limit > 0 else 1

    return ConversationListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=ConversationDetailResponse,
    summary="Get conversation details with paginated messages in chronological order",
)
async def get_conversation_detail(
    id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    validate_uuid(id)

    stmt = select(Conversation).where(Conversation.id == id)
    res = await db.execute(stmt)
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    # Count total messages
    count_stmt = select(func.count(Message.id)).where(Message.conversation_id == id)
    c_res = await db.execute(count_stmt)
    total_messages = c_res.scalar_one()

    # Paginate messages in chronological order
    offset = (page - 1) * limit
    msg_stmt = (
        select(Message)
        .where(Message.conversation_id == id)
        .order_by(Message.timestamp.asc())
        .offset(offset)
        .limit(limit)
    )
    m_res = await db.execute(msg_stmt)
    messages = m_res.scalars().all()

    formatted_msgs = []
    for m in messages:
        deliv = "delivered"
        if m.raw_payload and isinstance(m.raw_payload, dict):
            deliv = m.raw_payload.get("delivery_status", "delivered")
        formatted_msgs.append(
            MessageDetail(
                id=m.id,
                conversation_id=m.conversation_id,
                channel=m.channel,
                external_message_id=m.external_message_id,
                direction=m.direction,
                sender_type=m.sender_type,
                text=m.text,
                timestamp=m.timestamp,
                created_at=m.created_at,
                delivery_status=deliv,
            )
        )

    return ConversationDetailResponse(
        id=conv.id,
        channel=conv.channel,
        external_user_id=conv.external_user_id,
        customer_name=conv.customer_name,
        status=conv.status,
        ai_enabled=conv.ai_enabled,
        sentiment=conv.sentiment,
        intent=conv.intent,
        unread_count=conv.unread_count,
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=formatted_msgs,
        total_messages=total_messages,
        page=page,
        limit=limit,
    )


@router.post(
    "/{id}/read",
    summary="Reset conversation unread count to 0",
)
async def mark_conversation_as_read(
    id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    validate_uuid(id)

    stmt = select(Conversation).where(Conversation.id == id)
    res = await db.execute(stmt)
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    conv.unread_count = 0
    await db.commit()

    try:
        await connection_manager.broadcast("conversation.updated", serialize_conversation(conv))
    except Exception as exc:
        logger.warning("Failed to broadcast conversation read update", error=str(exc))

    return {"status": "ok", "unread_count": 0}


@router.patch(
    "/{id}",
    response_model=ConversationListItem,
    summary="Update conversation status, ai_enabled, sentiment, or intent (Human Takeover)",
)
async def update_conversation(
    id: str,
    payload: ConversationUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    validate_uuid(id)

    stmt = select(Conversation).where(Conversation.id == id)
    res = await db.execute(stmt)
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    if payload.status is not None:
        conv.status = payload.status
    if payload.ai_enabled is not None:
        conv.ai_enabled = payload.ai_enabled
    if payload.sentiment is not None:
        conv.sentiment = payload.sentiment
    if payload.intent is not None:
        conv.intent = payload.intent

    await db.commit()

    try:
        await connection_manager.broadcast("conversation.updated", serialize_conversation(conv))
    except Exception as exc:
        logger.warning("Failed to broadcast conversation update", error=str(exc))

    return ConversationListItem(
        id=conv.id,
        channel=conv.channel,
        external_user_id=conv.external_user_id,
        customer_name=conv.customer_name,
        status=conv.status,
        ai_enabled=conv.ai_enabled,
        sentiment=conv.sentiment,
        intent=conv.intent,
        unread_count=conv.unread_count,
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.post(
    "/{id}/reply",
    response_model=MessageDetail,
    summary="Send a manual reply through the channel adapter (Human Takeover)",
)
async def send_human_reply(
    id: str,
    payload: HumanReplyRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Sends a manual human agent reply to the customer through the channel adapter.
    Stores the reply with sender_type='human'.
    Automatically sets ai_enabled=False for that conversation unless keep_ai_enabled=True.
    """
    validate_uuid(id)

    stmt = select(Conversation).where(Conversation.id == id)
    res = await db.execute(stmt)
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    # 1. Send outbound message through the channel adapter
    adapter = channel_registry.get(conv.channel)
    delivery_success = True
    if adapter:
        try:
            delivery_success = await adapter.send_message(
                recipient_id=conv.external_user_id,
                text=payload.text,
                db=db,
            )
        except Exception as exc:
            logger.error("Human reply delivery failed", error=str(exc))
            delivery_success = False

    # 2. Automatically pause AI on human intervention unless keep_ai_enabled is True
    if not payload.keep_ai_enabled:
        conv.ai_enabled = False

    if conv.status == "needs_human":
        conv.status = "open"

    now = datetime.now(timezone.utc)
    conv.last_message_at = now

    # 3. Store message with delivery status
    human_msg = Message(
        conversation_id=conv.id,
        channel=conv.channel,
        external_message_id=f"human-{uuid.uuid4()}",
        direction="outbound",
        sender_type="human",
        text=payload.text,
        timestamp=now,
        raw_payload={
            "sender_user_id": current_user.id,
            "sender_name": current_user.full_name,
            "delivery_status": "delivered" if delivery_success else "failed",
        },
    )
    db.add(human_msg)
    await db.commit()
    await db.refresh(human_msg)

    # 4. Broadcast real-time events
    try:
        await connection_manager.broadcast("message.new", serialize_message(human_msg))
        await connection_manager.broadcast("conversation.updated", serialize_conversation(conv))
    except Exception as exc:
        logger.warning("Failed to broadcast human reply event", error=str(exc))

    if not delivery_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to deliver message to channel platform. Message saved as failed.",
        )

    return MessageDetail(
        id=human_msg.id,
        conversation_id=human_msg.conversation_id,
        channel=human_msg.channel,
        external_message_id=human_msg.external_message_id,
        direction=human_msg.direction,
        sender_type=human_msg.sender_type,
        text=human_msg.text,
        timestamp=human_msg.timestamp,
        created_at=human_msg.created_at,
        delivery_status="delivered",
    )
