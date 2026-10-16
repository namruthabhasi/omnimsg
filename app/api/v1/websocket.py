from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.security import decode_token
from app.models.user import User
from app.websockets.connection_manager import connection_manager

logger = structlog.get_logger()

router = APIRouter(tags=["WebSockets"])

AUTH_TIMEOUT_SECONDS = 5.0


async def authenticate_token(token: str) -> Optional[tuple[User, datetime]]:
    """
    Validates an access JWT token against the DB.
    Returns (User, exp_datetime) if valid and user is active, else None.
    """
    try:
        payload = decode_token(token)
    except JWTError:
        return None

    if payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    exp_epoch = payload.get("exp")
    if not user_id or not exp_epoch:
        return None

    exp_dt = datetime.fromtimestamp(exp_epoch, tz=timezone.utc)
    if exp_dt < datetime.now(timezone.utc):
        return None

    async with async_session_factory() as session:
        stmt = select(User).where(User.id == user_id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        if not user or not user.is_active:
            return None
        return user, exp_dt


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Authenticated real-time WebSocket endpoint.

    Handshake protocol:
    1. Client connects via WS.
    2. Client MUST send `{"type": "auth", "token": "<access_jwt>"}` within 5 seconds.
    3. Server verifies token and user status:
       - On failure/timeout: Closes connection with code 1008 (Policy Violation).
       - On success: Sends `{"type": "auth.ok"}`.
    4. Client can send `{"type": "ping"}` for heartbeat, server returns `{"type": "pong"}`.
    5. Client can send `{"type": "auth", "token": "<new_jwt>"}` to re-authenticate / refresh token.
    """
    await websocket.accept()

    # 1. First-message authentication handshake with timeout
    try:
        raw_message = await asyncio.wait_for(
            websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("WebSocket auth handshake timed out")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth timeout")
        return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.warning("Error receiving WebSocket auth message", error=str(exc))
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth error")
        return

    try:
        data = json.loads(raw_message)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid JSON")
        return

    # Reject any message type other than 'auth'
    if data.get("type") != "auth" or not data.get("token"):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="First message must be an auth message",
        )
        return

    auth_result = await authenticate_token(data["token"])
    if not auth_result:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired token",
        )
        return

    current_user, token_exp = auth_result
    await connection_manager.connect(websocket, current_user.id, token_exp)

    # Handshake succeeded
    await websocket.send_json({"type": "auth.ok"})

    # 2. Main message loop
    try:
        while True:
            # Check for token expiration
            if connection_manager.is_token_expired(websocket):
                logger.info("WebSocket token expired, closing connection", user_id=current_user.id)
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Token expired",
                )
                break

            msg_text = await websocket.receive_text()
            try:
                msg_data = json.loads(msg_text)
            except Exception:
                continue

            msg_type = msg_data.get("type")

            # Heartbeat ping
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            # Re-authentication with fresh access token
            elif msg_type == "auth":
                new_token = msg_data.get("token")
                if new_token:
                    reauth_res = await authenticate_token(new_token)
                    if reauth_res:
                        _, new_exp = reauth_res
                        connection_manager.update_token_expiry(websocket, new_exp)
                        await websocket.send_json({"type": "auth.ok"})
                    else:
                        await websocket.close(
                            code=status.WS_1008_POLICY_VIOLATION,
                            reason="Re-auth failed: invalid or expired token",
                        )
                        break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket exception in event loop", error=str(exc))
    finally:
        await connection_manager.disconnect(websocket)
