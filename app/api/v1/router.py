from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.demo import router as demo_router
from app.api.v1.knowledge_base import router as kb_router
from app.api.v1.settings import router as settings_router
from app.api.v1.simulator import router as simulator_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.websocket import router as ws_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(demo_router)
v1_router.include_router(auth_router)
v1_router.include_router(conversations_router)
v1_router.include_router(dashboard_router)
v1_router.include_router(settings_router)
v1_router.include_router(kb_router)
v1_router.include_router(webhooks_router)
v1_router.include_router(ws_router)
v1_router.include_router(simulator_router)
