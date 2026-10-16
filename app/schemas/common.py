from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Consistent error envelope returned by all endpoints."""
    detail: str


class MessageResponse(BaseModel):
    """Generic success message."""
    message: str
