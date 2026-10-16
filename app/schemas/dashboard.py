from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel


class StatusCounts(BaseModel):
    open: int
    needs_human: int
    closed: int


class ChannelMessageCounts(BaseModel):
    inbound: int
    outbound: int
    total: int


class DailyCount(BaseModel):
    date: str
    count: int


class DashboardStatsResponse(BaseModel):
    total_conversations: int
    status_counts: StatusCounts
    messages_per_channel: Dict[str, ChannelMessageCounts]
    total_messages: int
    ai_replies: int
    human_replies: int
    ai_vs_human_ratio: float
    average_response_time_seconds: Optional[float] = None
    needs_human_count: int
    conversations_per_day: List[DailyCount]
