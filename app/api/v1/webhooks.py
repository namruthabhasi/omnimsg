from __future__ import annotations

import hmac
import json
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tasks import process_ai_reply_task
from app.channels.manager import channel_registry
from app.channels.meta_base import BaseMetaAdapter
from app.channels.telegram import TelegramAdapter
from app.core.database import get_db
from app.services.message_pipeline import MessagePipelineService

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ─── Telegram Webhook ────────────────────────────────────────────────────────


@router.post("/telegram", summary="Telegram Bot API webhook endpoint")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_telegram_bot_api_secret_token: Annotated[
        Optional[str], Header()
    ] = None,
):
    adapter = channel_registry.get_or_raise("telegram")
    if not isinstance(adapter, TelegramAdapter):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid Telegram adapter configuration",
        )

    if not x_telegram_bot_api_secret_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing secret token header",
        )

    _, expected_secret = await adapter.get_credentials(db)
    if not expected_secret or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, expected_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid secret token",
        )

    try:
        payload = await request.json()
    except Exception:
        return {"status": "invalid_json"}

    normalized_messages = await adapter.parse_webhook(payload)
    if not normalized_messages:
        return {"status": "ignored"}

    pipeline = MessagePipelineService(db)
    for norm in normalized_messages:
        msg, is_duplicate, _ = await pipeline.ingest_inbound_message(norm)
        await db.commit()
        if not is_duplicate and msg is not None:
            background_tasks.add_task(process_ai_reply_task, msg.id)

    return {"status": "ok"}


# ─── Meta Generic Handlers ───────────────────────────────────────────────────


async def _handle_meta_get(
    channel_name: str,
    hub_mode: Optional[str],
    hub_verify_token: Optional[str],
    hub_challenge: Optional[str],
    db: AsyncSession,
) -> Response:
    adapter = channel_registry.get(channel_name)
    if not isinstance(adapter, BaseMetaAdapter):
        raise HTTPException(status_code=404, detail=f"Channel {channel_name} not supported")

    challenge = await adapter.verify_get_challenge(
        mode=hub_mode,
        verify_token=hub_verify_token,
        challenge=hub_challenge,
        db=db,
    )
    if not challenge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification token mismatch",
        )
    return PlainTextResponse(challenge, status_code=200)


async def _handle_meta_post(
    channel_name: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> dict:
    adapter = channel_registry.get(channel_name)
    if not isinstance(adapter, BaseMetaAdapter):
        raise HTTPException(status_code=404, detail=f"Channel {channel_name} not supported")

    # 1. Verify X-Hub-Signature-256 against raw body before json parsing
    raw_body = await request.body()
    is_valid = await adapter.verify_request(request, raw_body=raw_body, db=db)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Hub-Signature-256",
        )

    # 2. Parse payload safely (return 200 on malformed, never 500)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        logger.warning("Malformed JSON in Meta webhook", channel=channel_name, error=str(exc))
        return {"status": "invalid_payload"}

    normalized_messages = await adapter.parse_webhook(payload)
    if not normalized_messages:
        return {"status": "ignored"}

    pipeline = MessagePipelineService(db)

    # 3. Process every message in batched payload
    for norm in normalized_messages:
        msg, is_duplicate, _ = await pipeline.ingest_inbound_message(norm)
        await db.commit()
        if not is_duplicate and msg is not None:
            background_tasks.add_task(process_ai_reply_task, msg.id)

    return {"status": "ok"}


# ─── WhatsApp Webhooks ───────────────────────────────────────────────────────


@router.get("/whatsapp", summary="WhatsApp Cloud API GET webhook challenge verification")
async def whatsapp_webhook_verification(
    db: Annotated[AsyncSession, Depends(get_db)],
    hub_mode: Annotated[Optional[str], Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[Optional[str], Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[Optional[str], Query(alias="hub.challenge")] = None,
):
    return await _handle_meta_get("whatsapp", hub_mode, hub_verify_token, hub_challenge, db)


@router.post("/whatsapp", summary="WhatsApp Cloud API POST webhook receiver")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _handle_meta_post("whatsapp", request, background_tasks, db)


# ─── Instagram Webhooks ──────────────────────────────────────────────────────


@router.get("/instagram", summary="Instagram Direct GET webhook challenge verification")
async def instagram_webhook_verification(
    db: Annotated[AsyncSession, Depends(get_db)],
    hub_mode: Annotated[Optional[str], Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[Optional[str], Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[Optional[str], Query(alias="hub.challenge")] = None,
):
    return await _handle_meta_get("instagram", hub_mode, hub_verify_token, hub_challenge, db)


@router.post("/instagram", summary="Instagram Direct POST webhook receiver")
async def instagram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _handle_meta_post("instagram", request, background_tasks, db)


# ─── Messenger Webhooks ──────────────────────────────────────────────────────


@router.get("/messenger", summary="Facebook Messenger GET webhook challenge verification")
async def messenger_webhook_verification(
    db: Annotated[AsyncSession, Depends(get_db)],
    hub_mode: Annotated[Optional[str], Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[Optional[str], Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[Optional[str], Query(alias="hub.challenge")] = None,
):
    return await _handle_meta_get("messenger", hub_mode, hub_verify_token, hub_challenge, db)


@router.post("/messenger", summary="Facebook Messenger POST webhook receiver")
async def messenger_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _handle_meta_post("messenger", request, background_tasks, db)
