from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    global_ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    system_prompt: Mapped[str] = mapped_column(
        Text, default="You are a helpful customer service agent.", nullable=False
    )
    fallback_message: Mapped[str] = mapped_column(
        Text,
        default="I'm connecting you with a human agent who can help you better. Please hold on.",
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
