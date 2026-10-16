from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ChannelConfigOut(BaseModel):
    id: str
    channel: str
    is_active: bool
    credentials: Dict[str, str]  # Redacted values, e.g. "••••1234"
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChannelConfigUpdate(BaseModel):
    is_active: Optional[bool] = None
    # Generic key-value pairs; omitted or empty string retains existing value
    credentials: Optional[Dict[str, str]] = None


class SystemSettingsOut(BaseModel):
    global_ai_enabled: bool
    system_prompt: str
    fallback_message: str
    confidence_threshold: float
    updated_at: datetime


class SystemSettingsUpdate(BaseModel):
    global_ai_enabled: Optional[bool] = None
    system_prompt: Optional[str] = None
    fallback_message: Optional[str] = None
    confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
