from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ChannelConfig(Base, TimestampMixin):
    __tablename__ = "channel_configs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    channel: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
