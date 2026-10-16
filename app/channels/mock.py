from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Request

from app.channels.base import BaseChannelAdapter
from app.schemas.message import NormalizedMessage


class MockChannelAdapter(BaseChannelAdapter):
    """Mock channel adapter for unit tests and local simulation."""

    def __init__(self) -> None:
        self.sent_messages: List[Dict[str, Any]] = []
        self.should_verify_pass: bool = True
        self.should_send_pass: bool = True

    def reset(self) -> None:
        self.sent_messages.clear()
        self.should_verify_pass = True
        self.should_send_pass = True

    async def verify_request(self, request: Request) -> bool:
        return self.should_verify_pass

    async def parse_webhook(self, payload: dict | bytes) -> List[NormalizedMessage]:
        if isinstance(payload, bytes):
            import json
            payload = json.loads(payload.decode("utf-8"))

        # If payload is already structured as normalized or has nested entries
        if "messages" in payload:
            return [
                NormalizedMessage.model_validate(msg)
                for msg in payload["messages"]
            ]
        elif "text" in payload and "external_user_id" in payload:
            return [
                NormalizedMessage(
                    channel=payload.get("channel", "mock"),
                    external_user_id=payload["external_user_id"],
                    external_message_id=payload.get("external_message_id", f"mock-{datetime.now(timezone.utc).timestamp()}"),
                    customer_name=payload.get("customer_name"),
                    text=payload["text"],
                    timestamp=payload.get("timestamp") or datetime.now(timezone.utc),
                    direction=payload.get("direction", "inbound"),
                    sender_type=payload.get("sender_type", "customer"),
                    raw_payload=payload,
                )
            ]
        return []

    async def send_message(self, recipient_id: str, text: str, db=None, **kwargs) -> bool:
        if not self.should_send_pass:
            return False
        self.sent_messages.append({"recipient_id": recipient_id, "text": text})
        return True
