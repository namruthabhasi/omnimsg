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


class WhatsAppAdapter(BaseMetaAdapter):
    """WhatsApp Cloud API adapter."""

    def __init__(
        self,
        app_secret: Optional[str] = None,
        verify_token: Optional[str] = None,
        access_token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            channel_name="whatsapp",
            app_secret=app_secret,
            verify_token=verify_token,
            access_token=access_token,
            simulation_mode=simulation_mode,
        )
        self._phone_number_id = phone_number_id

    async def parse_webhook(self, payload: dict | bytes) -> List[NormalizedMessage]:
        """
        Parses WhatsApp Cloud API webhook payload.
        Extracts text messages from entry[].changes[].value.messages[].
        Extracts sender name from contacts[].
        Safely ignores statuses (delivery/read receipts) and non-text messages.
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
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})

                # Ignore delivery / read statuses
                if "statuses" in value and not value.get("messages"):
                    continue

                # Build profile name lookup from contacts
                contacts = value.get("contacts", [])
                contact_names: Dict[str, str] = {}
                for contact in contacts:
                    wa_id = contact.get("wa_id")
                    profile_name = contact.get("profile", {}).get("name")
                    if wa_id and profile_name:
                        contact_names[wa_id] = profile_name

                # Parse inbound messages
                messages = value.get("messages", [])
                for msg in messages:
                    # Ignore non-text messages (e.g., image, audio, video, sticker, location)
                    msg_type = msg.get("type")
                    if msg_type != "text":
                        continue

                    text_body = msg.get("text", {}).get("body")
                    if not text_body:
                        continue

                    msg_id = msg.get("id")
                    sender_id = msg.get("from")
                    if not msg_id or not sender_id:
                        continue

                    # Parse timestamp (epoch seconds)
                    ts_str = msg.get("timestamp")
                    if ts_str:
                        try:
                            msg_dt = datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
                        except (ValueError, TypeError):
                            msg_dt = datetime.now(timezone.utc)
                    else:
                        msg_dt = datetime.now(timezone.utc)

                    customer_name = contact_names.get(sender_id)

                    messages_out.append(
                        NormalizedMessage(
                            channel="whatsapp",
                            external_user_id=str(sender_id),
                            external_message_id=str(msg_id),
                            customer_name=customer_name,
                            text=text_body,
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
        Sends outbound WhatsApp message using WhatsApp Cloud API.
        If simulation_mode is enabled, records message without calling external network.
        Handles the 24-hour customer service window error by returning False.
        """
        if self.simulation_mode or settings.ENABLE_SIMULATOR:
            self.sent_messages.append(
                {"recipient_id": recipient_id, "text": text, "timestamp": datetime.now(timezone.utc)}
            )
            logger.info("WhatsApp simulated outbound send recorded", recipient_id=recipient_id)
            return True

        _, _, access_token, phone_number_id = await self.get_credentials(db)
        active_phone_id = self._phone_number_id or phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID

        if not access_token or not active_phone_id:
            logger.error("Missing WhatsApp access_token or phone_number_id")
            return False

        url = f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}/{active_phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code != 200:
                    data = resp.json()
                    error_data = data.get("error", {})
                    error_code = error_data.get("code")
                    error_subcode = error_data.get("error_subcode")

                    # Check for 24-hour service window expiry (Meta code 131047)
                    if error_subcode == 131047 or "24 hour" in str(error_data).lower():
                        logger.warning(
                            "WhatsApp send failed: outside 24-hour customer service window",
                            recipient_id=recipient_id,
                        )
                    else:
                        logger.error(
                            "WhatsApp Graph API returned error",
                            status_code=resp.status_code,
                            error_code=error_code,
                        )
                    return False
                return True
        except Exception as exc:
            logger.error("WhatsApp outbound request failed", error=str(exc))
            return False
