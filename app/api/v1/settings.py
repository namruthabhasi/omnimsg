from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Dict, List

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.config import settings
from app.core.database import get_db
from app.core.encryption import decrypt_value, encrypt_value
from app.models.channel_config import ChannelConfig
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.schemas.settings import (
    ChannelConfigOut,
    ChannelConfigUpdate,
    SystemSettingsOut,
    SystemSettingsUpdate,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/settings", tags=["Settings"])

SUPPORTED_CHANNELS = ["whatsapp", "instagram", "telegram", "messenger"]


def redact_secret(val: str) -> str:
    """Redacts sensitive credentials, showing only the last 4 characters."""
    if not val:
        return ""
    if len(val) <= 4:
        return "••••"
    return f"••••{val[-4:]}"


# ─── Channel Settings (Admin Only) ───────────────────────────────────────────


@router.get(
    "/channels",
    response_model=List[ChannelConfigOut],
    summary="List all channels with redacted credentials (Admin only)",
)
async def list_channel_settings(
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Returns channel configuration for all channels.
    All credentials are encrypted at rest and REDACTED in the API response.
    Never returns plaintext secrets.
    """
    stmt = select(ChannelConfig)
    res = await db.execute(stmt)
    configs = {c.channel: c for c in res.scalars().all()}

    output = []
    for ch in SUPPORTED_CHANNELS:
        cfg = configs.get(ch)
        redacted_creds: Dict[str, str] = {}
        is_active = True
        updated_at = datetime.now(timezone.utc)
        cfg_id = f"cfg-{ch}"

        if cfg:
            cfg_id = cfg.id
            is_active = cfg.is_active
            updated_at = cfg.updated_at
            if cfg.credentials_encrypted:
                try:
                    decrypted = decrypt_value(cfg.credentials_encrypted)
                    raw_creds = json.loads(decrypted)
                    redacted_creds = {k: redact_secret(str(v)) for k, v in raw_creds.items()}
                except Exception as exc:
                    logger.warning("Failed to decrypt credentials for channel", channel=ch, error=str(exc))
                    redacted_creds = {"error": "decryption_failed"}

        output.append(
            ChannelConfigOut(
                id=cfg_id,
                channel=ch,
                is_active=is_active,
                credentials=redacted_creds,
                updated_at=updated_at,
            )
        )

    return output


@router.put(
    "/channels/{channel}",
    response_model=ChannelConfigOut,
    summary="Create or update channel credentials (Admin only)",
)
async def update_channel_settings(
    channel: str,
    payload: ChannelConfigUpdate,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Updates channel credentials and active status.
    Credentials are encrypted at rest with Fernet.
    Omitted or empty fields retain their existing values.
    Returns redacted credentials.
    """
    channel = channel.lower()
    if channel not in SUPPORTED_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported channel '{channel}'. Supported: {SUPPORTED_CHANNELS}",
        )

    stmt = select(ChannelConfig).where(ChannelConfig.channel == channel)
    res = await db.execute(stmt)
    cfg = res.scalar_one_or_none()

    existing_creds: Dict[str, str] = {}
    if cfg and cfg.credentials_encrypted:
        try:
            decrypted = decrypt_value(cfg.credentials_encrypted)
            existing_creds = json.loads(decrypted)
        except Exception:
            existing_creds = {}

    # Merge payload credentials with existing credentials
    if payload.credentials:
        for k, v in payload.credentials.items():
            if v:  # Only update non-empty fields
                existing_creds[k] = v

    if not cfg:
        cfg = ChannelConfig(
            channel=channel,
            is_active=payload.is_active if payload.is_active is not None else True,
            credentials_encrypted=encrypt_value(json.dumps(existing_creds)),
        )
        db.add(cfg)
    else:
        if payload.is_active is not None:
            cfg.is_active = payload.is_active
        if existing_creds:
            cfg.credentials_encrypted = encrypt_value(json.dumps(existing_creds))

    await db.commit()
    await db.refresh(cfg)

    # Prepare redacted response
    redacted_creds = {k: redact_secret(str(v)) for k, v in existing_creds.items()}

    return ChannelConfigOut(
        id=cfg.id,
        channel=cfg.channel,
        is_active=cfg.is_active,
        credentials=redacted_creds,
        updated_at=cfg.updated_at,
    )


# ─── System Settings (Admin Only) ───────────────────────────────────────────


@router.get(
    "/system",
    response_model=SystemSettingsOut,
    summary="Get global AI and system prompt settings (Admin only)",
)
async def get_system_settings(
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = select(SystemSettings).limit(1)
    res = await db.execute(stmt)
    sys_settings = res.scalar_one_or_none()

    if not sys_settings:
        return SystemSettingsOut(
            global_ai_enabled=True,
            system_prompt=settings.AI_SYSTEM_PROMPT,
            fallback_message=settings.AI_FALLBACK_MESSAGE,
            confidence_threshold=settings.AI_CONFIDENCE_THRESHOLD,
            updated_at=datetime.now(timezone.utc),
        )

    return SystemSettingsOut(
        global_ai_enabled=sys_settings.global_ai_enabled,
        system_prompt=sys_settings.system_prompt,
        fallback_message=sys_settings.fallback_message,
        confidence_threshold=settings.AI_CONFIDENCE_THRESHOLD,
        updated_at=sys_settings.updated_at,
    )


@router.put(
    "/system",
    response_model=SystemSettingsOut,
    summary="Update global AI, system prompt, fallback message, and confidence threshold (Admin only)",
)
async def update_system_settings(
    payload: SystemSettingsUpdate,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = select(SystemSettings).limit(1)
    res = await db.execute(stmt)
    sys_settings = res.scalar_one_or_none()

    if not sys_settings:
        sys_settings = SystemSettings(
            global_ai_enabled=True,
            system_prompt=settings.AI_SYSTEM_PROMPT,
            fallback_message=settings.AI_FALLBACK_MESSAGE,
        )
        db.add(sys_settings)

    if payload.global_ai_enabled is not None:
        sys_settings.global_ai_enabled = payload.global_ai_enabled
    if payload.system_prompt is not None:
        sys_settings.system_prompt = payload.system_prompt
    if payload.fallback_message is not None:
        sys_settings.fallback_message = payload.fallback_message
    if payload.confidence_threshold is not None:
        settings.AI_CONFIDENCE_THRESHOLD = payload.confidence_threshold

    await db.commit()
    await db.refresh(sys_settings)

    return SystemSettingsOut(
        global_ai_enabled=sys_settings.global_ai_enabled,
        system_prompt=sys_settings.system_prompt,
        fallback_message=sys_settings.fallback_message,
        confidence_threshold=settings.AI_CONFIDENCE_THRESHOLD,
        updated_at=sys_settings.updated_at,
    )
