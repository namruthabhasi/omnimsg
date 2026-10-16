from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import structlog
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import BaseChannelAdapter
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.encryption import decrypt_value
from app.models.channel_config import ChannelConfig
from app.schemas.message import NormalizedMessage

logger = structlog.get_logger()

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """Split text into chunks of at most max_length characters without breaking mid-word when possible."""
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split at a newline within limit
        split_idx = remaining.rfind("\n", 0, max_length)
        if split_idx == -1:
            # Try to split at whitespace
            split_idx = remaining.rfind(" ", 0, max_length)
        if split_idx <= 0:
            # Hard cutoff if no space or newline found
            split_idx = max_length

        chunk = remaining[:split_idx].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].lstrip()

    return chunks


async def resolve_telegram_credentials(
    db: Optional[AsyncSession] = None,
) -> Tuple[str, str]:
    """
    Resolves Telegram bot token and webhook secret.
    Checks ChannelConfig in DB first (decrypted); falls back to settings.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    webhook_secret = settings.TELEGRAM_WEBHOOK_SECRET

    if db is not None:
        try:
            stmt = select(ChannelConfig).where(ChannelConfig.channel == "telegram")
            res = await db.execute(stmt)
            config = res.scalar_one_or_none()
            if config and config.is_active and config.credentials_encrypted:
                decrypted_json = decrypt_value(config.credentials_encrypted)
                creds = json.loads(decrypted_json)
                if creds.get("bot_token"):
                    bot_token = creds["bot_token"]
                if creds.get("webhook_secret"):
                    webhook_secret = creds["webhook_secret"]
        except Exception as exc:
            logger.warning("Could not load Telegram credentials from DB", error=str(exc))

    return bot_token, webhook_secret


class TelegramAdapter(BaseChannelAdapter):
    """Channel adapter for Telegram Bot API."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> None:
        self._bot_token = bot_token
        self._webhook_secret = webhook_secret

    async def get_credentials(self, db: Optional[AsyncSession] = None) -> Tuple[str, str]:
        if self._bot_token and self._webhook_secret:
            return self._bot_token, self._webhook_secret
        token, secret = await resolve_telegram_credentials(db)
        return self._bot_token or token, self._webhook_secret or secret

    async def verify_request(self, request: Request, db: Optional[AsyncSession] = None) -> bool:
        """
        Verify incoming webhook request via X-Telegram-Bot-Api-Secret-Token header.
        Uses constant-time comparison (hmac.compare_digest).
        """
        header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not header_token:
            return False

        _, expected_secret = await self.get_credentials(db)
        if not expected_secret:
            return False

        return hmac.compare_digest(header_token, expected_secret)

    async def parse_webhook(self, payload: dict | bytes) -> List[NormalizedMessage]:
        """
        Parses Telegram Update object.
        Returns NormalizedMessage if text message from customer; otherwise returns [].
        """
        if isinstance(payload, bytes):
            payload = json.loads(payload.decode("utf-8"))

        # Telegram sends an update object
        # Non-text updates or edited messages, channel posts, etc. are ignored safely
        message = payload.get("message")
        if not message or not isinstance(message, dict):
            return []

        # Ignore bot's own messages
        from_user = message.get("from", {})
        if from_user.get("is_bot", False):
            return []

        # Ignore group events (only private chats handled for customer messaging)
        chat = message.get("chat", {})
        if chat.get("type") not in ("private", None):
            return []

        # Only process text messages
        text = message.get("text")
        if not text:
            return []

        chat_id = str(chat.get("id", ""))
        message_id = message.get("message_id")
        if not chat_id or message_id is None:
            return []

        # Build unique external message id
        external_message_id = f"{chat_id}:{message_id}"

        # Construct customer name
        first_name = from_user.get("first_name", "").strip()
        last_name = from_user.get("last_name", "").strip()
        username = from_user.get("username", "").strip()

        name_parts = [p for p in (first_name, last_name) if p]
        full_name = " ".join(name_parts) if name_parts else None
        if username:
            if full_name:
                customer_name = f"{full_name} (@{username})"
            else:
                customer_name = f"@{username}"
        else:
            customer_name = full_name

        # Parse unix timestamp
        date_epoch = message.get("date")
        if date_epoch:
            timestamp = datetime.fromtimestamp(date_epoch, tz=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        normalized = NormalizedMessage(
            channel="telegram",
            external_user_id=chat_id,
            external_message_id=external_message_id,
            customer_name=customer_name,
            text=text,
            timestamp=timestamp,
            direction="inbound",
            sender_type="customer",
            raw_payload=payload,
        )
        return [normalized]

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """
        Dispatches outbound message to Telegram recipient via Bot API sendMessage.
        Splits text if longer than 4096 characters.
        """
        bot_token, _ = await self.get_credentials(db)
        if not bot_token:
            logger.error("Cannot send Telegram message: bot token is not configured")
            return False

        chunks = split_message(text, TELEGRAM_MAX_MESSAGE_LENGTH)
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        all_success = True
        async with httpx.AsyncClient(timeout=10.0) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": recipient_id,
                    "text": chunk,
                }
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code != 200:
                        logger.error(
                            "Telegram sendMessage returned non-200",
                            status_code=resp.status_code,
                            # Never log the token
                            recipient_id=recipient_id,
                        )
                        all_success = False
                except Exception as exc:
                    logger.error(
                        "Telegram sendMessage request failed",
                        recipient_id=recipient_id,
                        error=str(exc),
                    )
                    all_success = False

        return all_success
