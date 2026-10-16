from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["Demo"])


@router.get("/demo-info", summary="Get public demo account information")
async def get_demo_info() -> dict[str, bool | str]:
    if not settings.DEMO_MODE:
        return {"demo": False, "email": ""}
    return {
        "demo": True,
        "email": "demo@example.com",
        "password": settings.DEMO_PASSWORD,
    }
