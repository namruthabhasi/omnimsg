from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.meta_base import BaseMetaAdapter
from app.core.config import settings
from app.schemas.message import NormalizedMessage

logger = structlog.get_logger()


class MessengerAdapter(BaseMetaAdapter):
    """Facebook Messenger adapter."""

    def __init__(
        self,
        app_secret: Optional[str] = None,
        verify_token: Optional[str] = None,
        access_token: Optional[str] = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            channel_name="messenger",
            app_secret=app_secret,
            verify_token=verify_token,
            access_token=access_token,
            simulation_mode=simulation_mode,
        )

    async def parse_webhook(self, payload: dict | bytes) -> List[NormalizedMessage]:
        """
        Parses Facebook Messenger webhook payload.
        Extracts messages from entry[].messaging[].
        Ignores echo events, delivery/read receipts, reactions, and postbacks.
        """
        if isinstance(payload, bytes):
            try:
                payload = json.loads(payload.decode("utf-8"))
            except Exception:
                return []

        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            return []

        messages_out: List[NormalizedMessage] = []

        for entry in entries:
            messaging_events = entry.get("messaging", [])
            for event in messaging_events:
                # Ignore delivery, read, postback, optin
                if "delivery" in event or "read" in event or "postback" in event:
                    continue

                msg = event.get("message")
                if not msg:
                    continue

                # Amendment 3: Ignore echo events
                if msg.get("is_echo", False):
                    continue

                text = msg.get("text")
                if not text:
                    continue

                sender_id = event.get("sender", {}).get("id")
                mid = msg.get("mid")
                if not sender_id or not mid:
                    continue

                # Parse timestamp (epoch milliseconds)
                ts_raw = event.get("timestamp")
                if ts_raw:
                    try:
                        msg_dt = datetime.fromtimestamp(ts_raw / 1000.0, tz=timezone.utc)
                    except Exception:
                        msg_dt = datetime.now(timezone.utc)
                else:
                    msg_dt = datetime.now(timezone.utc)

                messages_out.append(
                    NormalizedMessage(
                        channel="messenger",
                        external_user_id=str(sender_id),
                        external_message_id=str(mid),
                        customer_name=f"Messenger User ({sender_id})",
                        text=text,
                        timestamp=msg_dt,
                        direction="inbound",
                        sender_type="customer",
                        raw_payload=payload,
                    )
                )

        return messages_out

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """
        Dispatches outbound message to Messenger user via Graph API /me/messages.
        In simulation mode, records message and returns True without calling Meta.
        """
        if self.simulation_mode or settings.ENABLE_SIMULATOR:
            self.sent_messages.append(
                {"recipient_id": recipient_id, "text": text, "timestamp": datetime.now(timezone.utc)}
            )
            logger.info("Messenger simulated outbound send recorded", recipient_id=recipient_id)
            return True

        _, _, access_token, _ = await self.get_credentials(db)
        active_token = self._access_token or access_token or settings.MESSENGER_PAGE_ACCESS_TOKEN

        if not active_token:
            logger.error("Missing Messenger page access_token")
            return False

        url = f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}/me/messages"
        headers = {
            "Authorization": f"Bearer {active_token}",
            "Content-Type": "application/json",
        }
        body = {
            "recipient": {"id": recipient_id},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code != 200:
                    logger.error("Messenger Graph API send error", status_code=resp.status_code)
                    return False
                return True
        except Exception as exc:
            logger.error("Messenger outbound request failed", error=str(exc))
            return False
