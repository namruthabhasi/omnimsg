from __future__ import annotations

import structlog

from app.ai.agent import AIAgentService
from app.core.database import async_session_factory

logger = structlog.get_logger()


async def process_ai_reply_task(message_id: str) -> None:
    """Background task executing the AI reply flow with an isolated database session."""
    async with async_session_factory() as session:
        try:
            agent = AIAgentService(session)
            ai_msg = await agent.process_inbound_message(message_id)
            await session.commit()
            if ai_msg:
                logger.info(
                    "Background AI reply completed",
                    message_id=message_id,
                    ai_message_id=ai_msg.id,
                )
        except Exception as exc:
            await session.rollback()
            logger.error(
                "Background AI task failed",
                message_id=message_id,
                error=str(exc),
            )
