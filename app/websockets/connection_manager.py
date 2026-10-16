from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import structlog
from fastapi import WebSocket

from app.websockets.events import create_event

logger = structlog.get_logger()


class ConnectionManager:
    """
    Manages active authenticated WebSocket connections.

    NOTE: This is an in-memory, single-process implementation suitable for local
    development and single-instance deployments. For horizontal scaling across
    multiple application workers or servers, this connection registry would be
    replaced or backed by a distributed pub/sub broker (such as Redis Pub/Sub).
    """

    def __init__(self) -> None:
        # Maps user_id -> Set of active WebSockets (supports multiple tabs / devices per user)
        self._user_connections: Dict[str, Set[WebSocket]] = {}
        # Maps WebSocket -> dict with metadata: {"user_id": str, "token_exp": datetime}
        self._socket_metadata: Dict[WebSocket, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        token_exp: datetime,
    ) -> None:
        """Register an authenticated WebSocket connection."""
        async with self._lock:
            if user_id not in self._user_connections:
                self._user_connections[user_id] = set()
            self._user_connections[user_id].add(websocket)
            self._socket_metadata[websocket] = {
                "user_id": user_id,
                "token_exp": token_exp,
            }
        logger.info("WebSocket connected and authenticated", user_id=user_id)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the registry."""
        async with self._lock:
            meta = self._socket_metadata.pop(websocket, None)
            if meta:
                user_id = meta.get("user_id")
                if user_id and user_id in self._user_connections:
                    self._user_connections[user_id].discard(websocket)
                    if not self._user_connections[user_id]:
                        del self._user_connections[user_id]
        logger.info("WebSocket disconnected")

    def update_token_expiry(self, websocket: WebSocket, new_exp: datetime) -> bool:
        """Updates the token expiration for an open socket upon re-auth."""
        if websocket in self._socket_metadata:
            self._socket_metadata[websocket]["token_exp"] = new_exp
            return True
        return False

    def is_token_expired(self, websocket: WebSocket) -> bool:
        """Checks if the authenticated token for this socket has expired."""
        meta = self._socket_metadata.get(websocket)
        if not meta:
            return True
        exp: datetime = meta.get("token_exp")
        if not exp:
            return True
        now = datetime.now(timezone.utc)
        return exp.replace(tzinfo=timezone.utc) < now

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Broadcasts an event in the standard envelope to all active authenticated connections.
        One failed or disconnected client will never interrupt delivery to others.
        """
        envelope = create_event(event_type, data)
        dead_sockets: List[WebSocket] = []

        # Snapshot active sockets
        async with self._lock:
            active_sockets = list(self._socket_metadata.keys())

        for ws in active_sockets:
            try:
                await ws.send_json(envelope)
            except Exception as exc:
                logger.warning(
                    "Failed to deliver WebSocket message to client, marking for cleanup",
                    error=str(exc),
                )
                dead_sockets.append(ws)

        # Clean up any dead sockets
        if dead_sockets:
            for ws in dead_sockets:
                await self.disconnect(ws)

    async def send_to_user(
        self,
        user_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Sends an event specifically to all active connections for a given user."""
        envelope = create_event(event_type, data)
        dead_sockets: List[WebSocket] = []

        async with self._lock:
            user_sockets = list(self._user_connections.get(user_id, set()))

        for ws in user_sockets:
            try:
                await ws.send_json(envelope)
            except Exception as exc:
                logger.warning("Failed to send WebSocket message to user", user_id=user_id, error=str(exc))
                dead_sockets.append(ws)

        if dead_sockets:
            for ws in dead_sockets:
                await self.disconnect(ws)

    def get_active_count(self) -> int:
        """Total number of active authenticated connections."""
        return len(self._socket_metadata)

    def get_user_count(self) -> int:
        """Total number of unique authenticated users connected."""
        return len(self._user_connections)


connection_manager = ConnectionManager()
