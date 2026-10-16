from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from fastapi import Request

from app.schemas.message import NormalizedMessage


class BaseChannelAdapter(ABC):
    """Abstract base class for all platform channel adapters."""

    @abstractmethod
    async def verify_request(self, request: Request) -> bool:
        """Verify webhook signature or secret header/query token."""
        pass

    @abstractmethod
    async def parse_webhook(self, payload: dict | bytes) -> List[NormalizedMessage]:
        """Parse incoming vendor webhook payload into NormalizedMessage objects."""
        pass

    @abstractmethod
    async def send_message(self, recipient_id: str, text: str, db=None, **kwargs) -> bool:
        """Send an outbound text message to the channel recipient."""
        pass
