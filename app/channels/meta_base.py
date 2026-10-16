from __future__ import annotations

import hashlib
import hmac
import json
from typing import Optional, Tuple

import structlog
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import BaseChannelAdapter
from app.core.config import settings
from app.core.encryption import decrypt_value
from app.models.channel_config import ChannelConfig

logger = structlog.get_logger()


async def resolve_meta_credentials(
    channel_name: str,
    db: Optional[AsyncSession] = None,
) -> Tuple[str, str, str, str]:
    """
    Resolves Meta credentials for channel (whatsapp, instagram, messenger).
    Returns (app_secret, verify_token, access_token, phone_number_id).
    Checks DB ChannelConfig first; falls back to environment variables.
    """
    app_secret = settings.META_APP_SECRET
    verify_token = settings.META_VERIFY_TOKEN
    access_token = ""
    phone_number_id = ""

    if channel_name == "whatsapp":
        access_token = settings.WHATSAPP_ACCESS_TOKEN
        phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
    elif channel_name == "instagram":
        access_token = settings.INSTAGRAM_ACCESS_TOKEN
    elif channel_name == "messenger":
        access_token = settings.MESSENGER_PAGE_ACCESS_TOKEN

    if db is not None:
        try:
            stmt = select(ChannelConfig).where(ChannelConfig.channel == channel_name)
            res = await db.execute(stmt)
            config = res.scalar_one_or_none()
            if config and config.is_active and config.credentials_encrypted:
                decrypted_json = decrypt_value(config.credentials_encrypted)
                creds = json.loads(decrypted_json)
                if creds.get("app_secret"):
                    app_secret = creds["app_secret"]
                if creds.get("verify_token"):
                    verify_token = creds["verify_token"]
                if creds.get("access_token"):
                    access_token = creds["access_token"]
                if creds.get("phone_number_id"):
                    phone_number_id = creds["phone_number_id"]
        except Exception as exc:
            logger.warning("Could not load Meta credentials from DB", channel=channel_name, error=str(exc))

    return app_secret, verify_token, access_token, phone_number_id


class BaseMetaAdapter(BaseChannelAdapter):
    """Base class for Meta Graph API adapters (WhatsApp, Instagram, Messenger)."""

    def __init__(
        self,
        channel_name: str,
        app_secret: Optional[str] = None,
        verify_token: Optional[str] = None,
        access_token: Optional[str] = None,
        simulation_mode: bool = False,
    ) -> None:
        self.channel_name = channel_name
        self._app_secret = app_secret
        self._verify_token = verify_token
        self._access_token = access_token
        self.simulation_mode = simulation_mode
        self.sent_messages: list[dict] = []

    async def get_credentials(
        self, db: Optional[AsyncSession] = None
    ) -> Tuple[str, str, str, str]:
        app_sec, ver_tok, acc_tok, phone_id = await resolve_meta_credentials(
            self.channel_name, db
        )
        return (
            self._app_secret or app_sec,
            self._verify_token or ver_tok,
            self._access_token or acc_tok,
            phone_id,
        )

    async def verify_request(
        self,
        request: Request,
        raw_body: Optional[bytes] = None,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """
        Verify incoming webhook request via X-Hub-Signature-256 header.
        HMAC-SHA256 of the raw body using app_secret.
        """
        signature_header = request.headers.get("X-Hub-Signature-256")
        if not signature_header:
            logger.warning("Missing X-Hub-Signature-256 header", channel=self.channel_name)
            return False

        app_secret, _, _, _ = await self.get_credentials(db)
        if not app_secret:
            logger.warning("No app_secret configured for Meta channel", channel=self.channel_name)
            return False

        if raw_body is None:
            raw_body = await request.body()

        expected_signature = (
            "sha256="
            + hmac.new(
                app_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(signature_header, expected_signature)

    async def verify_get_challenge(
        self,
        mode: Optional[str],
        verify_token: Optional[str],
        challenge: Optional[str],
        db: Optional[AsyncSession] = None,
    ) -> Optional[str]:
        """
        Validates GET webhook challenge:
        Returns challenge string if valid; None otherwise.
        """
        if mode != "subscribe" or not verify_token or not challenge:
            return None

        _, expected_token, _, _ = await self.get_credentials(db)
        if not expected_token:
            return None

        if hmac.compare_digest(verify_token, expected_token):
            return challenge
        return None
