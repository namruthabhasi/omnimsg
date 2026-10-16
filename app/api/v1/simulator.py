from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tasks import process_ai_reply_task
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.message import NormalizedMessage
from app.schemas.simulator import SimulatorRequest, SimulatorResponse
from app.services.message_pipeline import MessagePipelineService

router = APIRouter(prefix="/simulator", tags=["Simulator"])


@router.post(
    "/send",
    response_model=SimulatorResponse,
    summary="Simulate an inbound customer message (admin, or agent in demo mode)",
)
async def simulate_inbound_message(
    payload: SimulatorRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Simulates an inbound message on WhatsApp, Instagram, Telegram, or Messenger.
    Requires admin privileges, or agent privileges when DEMO_MODE=true, and
    ENABLE_SIMULATOR=true in configuration.
    """
    if current_user.role != "admin" and not (
        settings.DEMO_MODE and current_user.role == "agent"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    if not settings.ENABLE_SIMULATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulator endpoint is disabled. Set ENABLE_SIMULATOR=true in environment.",
        )

    ext_msg_id = (
        payload.external_message_id
        or f"sim-{payload.channel}-{uuid.uuid4().hex[:12]}"
    )

    norm = NormalizedMessage(
        channel=payload.channel,
        external_user_id=payload.external_user_id,
        external_message_id=ext_msg_id,
        customer_name=payload.customer_name,
        text=payload.text,
        timestamp=datetime.now(timezone.utc),
        direction="inbound",
        sender_type="customer",
        raw_payload={"simulated": True, "by_user": current_user.email},
    )

    pipeline = MessagePipelineService(db)
    msg, is_dup, conv = await pipeline.ingest_inbound_message(norm)
    await db.commit()

    if is_dup or msg is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate message ID",
        )

    # Trigger background AI reply
    background_tasks.add_task(process_ai_reply_task, msg.id)

    return SimulatorResponse(
        status="delivered",
        channel=payload.channel,
        external_user_id=payload.external_user_id,
        external_message_id=ext_msg_id,
        conversation_id=conv.id,
        message_id=msg.id,
    )
