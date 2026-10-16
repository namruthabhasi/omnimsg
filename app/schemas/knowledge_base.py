from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class KBEntryCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1)
    category: Optional[str] = Field(None, max_length=100)
    is_active: bool = True


class KBEntryUpdate(BaseModel):
    question: Optional[str] = Field(None, min_length=1, max_length=500)
    answer: Optional[str] = Field(None, min_length=1)
    category: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class KBEntryOut(BaseModel):
    id: str
    question: str
    answer: str
    category: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KBListResponse(BaseModel):
    items: List[KBEntryOut]
    total: int
    page: int
    limit: int
    pages: int
