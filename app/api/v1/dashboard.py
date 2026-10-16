from __future__ import annotations

from datetime import datetime
from typing import Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.dashboard import (
    ChannelMessageCounts,
    DailyCount,
    DashboardStatsResponse,
    StatusCounts,
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


async def compute_avg_response_time(
    db: AsyncSession,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Optional[float]:
    """
    Computes average response time from an inbound message to the next outbound message
    in the same conversation using the SQL window function LEAD().
    Handles the zero-data case safely without errors.
    """
    lead_direction = func.lead(Message.direction).over(
        partition_by=Message.conversation_id,
        order_by=Message.timestamp,
    ).label("next_direction")

    lead_timestamp = func.lead(Message.timestamp).over(
        partition_by=Message.conversation_id,
        order_by=Message.timestamp,
    ).label("next_timestamp")

    subq_stmt = select(
        Message.conversation_id,
        Message.direction,
        Message.timestamp,
        lead_direction,
        lead_timestamp,
    )
    if start_date:
        subq_stmt = subq_stmt.where(Message.timestamp >= start_date)
    if end_date:
        subq_stmt = subq_stmt.where(Message.timestamp <= end_date)

    subq = subq_stmt.subquery()

    filter_stmt = select(
        subq.c.timestamp,
        subq.c.next_timestamp,
    ).where(
        subq.c.direction == "inbound",
        subq.c.next_direction == "outbound",
    )

    res = await db.execute(filter_stmt)
    rows = res.all()
    if not rows:
        return None

    deltas: List[float] = []
    for in_ts, out_ts in rows:
        if in_ts is not None and out_ts is not None:
            if isinstance(in_ts, str):
                in_ts = datetime.fromisoformat(in_ts)
            if isinstance(out_ts, str):
                out_ts = datetime.fromisoformat(out_ts)
            diff = (out_ts - in_ts).total_seconds()
            if diff >= 0:
                deltas.append(diff)

    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 2)


@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
    summary="Get aggregated messaging and AI response statistics",
)
async def get_dashboard_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
):
    """
    Aggregates dashboard statistics using SQL group-by queries:
    - Total conversations and counts by status (open, needs_human, closed)
    - Messages per channel (inbound and outbound)
    - AI vs Human reply counts and ratio
    - Average response time (inbound to next outbound)
    - Conversations per day
    """
    # 1. Total and Status Counts
    conv_stmt = select(Conversation.status, func.count(Conversation.id)).group_by(Conversation.status)
    if start_date:
        conv_stmt = conv_stmt.where(Conversation.created_at >= start_date)
    if end_date:
        conv_stmt = conv_stmt.where(Conversation.created_at <= end_date)

    conv_res = await db.execute(conv_stmt)
    status_map = dict(conv_res.all())

    open_count = status_map.get("open", 0)
    needs_human_count = status_map.get("needs_human", 0)
    closed_count = status_map.get("closed", 0)
    total_conversations = open_count + needs_human_count + closed_count

    # 2. Messages per channel & direction
    msg_stmt = select(
        Message.channel,
        Message.direction,
        func.count(Message.id),
    ).group_by(Message.channel, Message.direction)
    if start_date:
        msg_stmt = msg_stmt.where(Message.timestamp >= start_date)
    if end_date:
        msg_stmt = msg_stmt.where(Message.timestamp <= end_date)

    msg_res = await db.execute(msg_stmt)
    channel_counts: Dict[str, ChannelMessageCounts] = {}
    total_messages = 0

    for ch, direction, count in msg_res.all():
        total_messages += count
        if ch not in channel_counts:
            channel_counts[ch] = ChannelMessageCounts(inbound=0, outbound=0, total=0)
        counts = channel_counts[ch]
        if direction == "inbound":
            counts.inbound += count
        elif direction == "outbound":
            counts.outbound += count
        counts.total = counts.inbound + counts.outbound

    # Ensure all 4 main channels are represented
    for main_ch in ["whatsapp", "instagram", "telegram", "messenger"]:
        if main_ch not in channel_counts:
            channel_counts[main_ch] = ChannelMessageCounts(inbound=0, outbound=0, total=0)

    # 3. AI vs Human Reply Counts
    reply_stmt = (
        select(Message.sender_type, func.count(Message.id))
        .where(Message.direction == "outbound")
        .group_by(Message.sender_type)
    )
    if start_date:
        reply_stmt = reply_stmt.where(Message.timestamp >= start_date)
    if end_date:
        reply_stmt = reply_stmt.where(Message.timestamp <= end_date)

    reply_res = await db.execute(reply_stmt)
    sender_map = dict(reply_res.all())
    ai_replies = sender_map.get("ai", 0)
    human_replies = sender_map.get("human", 0)

    total_outbound = ai_replies + human_replies
    ratio = round(ai_replies / max(human_replies, 1), 2) if total_outbound > 0 else 0.0

    # 4. Average response time
    avg_response_time = await compute_avg_response_time(db, start_date, end_date)

    # 5. Conversations per day (last 30 days)
    daily_stmt = (
        select(func.date(Conversation.created_at).label("day"), func.count(Conversation.id))
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at).desc())
        .limit(30)
    )
    if start_date:
        daily_stmt = daily_stmt.where(Conversation.created_at >= start_date)
    if end_date:
        daily_stmt = daily_stmt.where(Conversation.created_at <= end_date)

    daily_res = await db.execute(daily_stmt)
    daily_counts = [
        DailyCount(date=str(d), count=c) for d, c in reversed(daily_res.all()) if d is not None
    ]

    return DashboardStatsResponse(
        total_conversations=total_conversations,
        status_counts=StatusCounts(
            open=open_count,
            needs_human=needs_human_count,
            closed=closed_count,
        ),
        messages_per_channel=channel_counts,
        total_messages=total_messages,
        ai_replies=ai_replies,
        human_replies=human_replies,
        ai_vs_human_ratio=ratio,
        average_response_time_seconds=avg_response_time,
        needs_human_count=needs_human_count,
        conversations_per_day=daily_counts,
    )
